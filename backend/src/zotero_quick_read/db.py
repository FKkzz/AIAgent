from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .schemas import JobRecord, JobSubmit


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class QueueDB:
    """Small WAL-backed persistent queue. Each method owns its SQLite connection."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    dedupe_key TEXT NOT NULL UNIQUE,
                    library_id INTEGER NOT NULL,
                    parent_key TEXT NOT NULL,
                    attachment_key TEXT,
                    attachment_path TEXT NOT NULL,
                    title TEXT NOT NULL,
                    abstract_mode INTEGER NOT NULL DEFAULT 0,
                    abstract_text TEXT,
                    fingerprint TEXT,
                    instruction_version TEXT NOT NULL,
                    model TEXT,
                    auth_mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    force_generation INTEGER NOT NULL DEFAULT 0,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    error_code TEXT,
                    message TEXT,
                    result_json TEXT,
                    next_attempt_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_claim
                    ON jobs(status, next_attempt_at, created_at);
                CREATE INDEX IF NOT EXISTS idx_jobs_parent
                    ON jobs(library_id, parent_key, created_at DESC);
                CREATE TABLE IF NOT EXISTS job_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    status TEXT NOT NULL,
                    code TEXT,
                    message TEXT,
                    created_at TEXT NOT NULL
                );
                """
            )

    def recover_interrupted(self) -> int:
        now = utc_now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT id FROM jobs WHERE status='processing'").fetchall()
            db.execute(
                """UPDATE jobs SET status='queued', message='后台重启后恢复排队',
                   next_attempt_at=NULL, updated_at=? WHERE status='processing'""",
                (now,),
            )
            for row in rows:
                self._event(db, row["id"], "queued", "restart_recovery", "后台重启后恢复排队")
            db.commit()
            return len(rows)

    def enqueue(
        self,
        request: JobSubmit,
        *,
        instruction_version: str,
        model: str | None,
        auth_mode: str,
        max_attempts: int,
        fingerprint: str | None,
    ) -> tuple[JobRecord, bool]:
        job_id = str(uuid.uuid4())
        basis = {
            "library_id": request.library_id,
            "parent_key": request.parent_key,
            "attachment_key": request.attachment_key,
            "fingerprint": fingerprint or f"pending:{Path(request.attachment_path)}",
            "instruction_version": instruction_version,
            "model": model,
            "auth_mode": auth_mode,
            "abstract_mode": request.abstract_mode,
        }
        stable_key = hashlib.sha256(
            json.dumps(basis, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        dedupe_key = f"force:{job_id}:{stable_key}" if request.force else stable_key
        attachment_ready = request.abstract_mode or Path(request.attachment_path).is_file()
        status = "queued" if attachment_ready else "waiting_fulltext"
        now = utc_now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM jobs WHERE dedupe_key=?", (dedupe_key,)).fetchone()
            if existing is not None:
                db.commit()
                return self._record(existing), False
            db.execute(
                """
                INSERT INTO jobs (
                    id, dedupe_key, library_id, parent_key, attachment_key, attachment_path,
                    title, abstract_mode, abstract_text, fingerprint, instruction_version,
                    model, auth_mode, status, max_attempts, force_generation,
                    next_attempt_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    dedupe_key,
                    request.library_id,
                    request.parent_key,
                    request.attachment_key,
                    request.attachment_path,
                    request.title,
                    int(request.abstract_mode),
                    request.abstract,
                    fingerprint,
                    instruction_version,
                    model,
                    auth_mode,
                    status,
                    max_attempts,
                    int(request.force),
                    now if status == "waiting_fulltext" else None,
                    now,
                    now,
                ),
            )
            self._event(db, job_id, status, None, "任务已创建")
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            db.commit()
            return self._record(row), True

    def claim_next(self) -> JobRecord | None:
        now = utc_now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """
                SELECT * FROM jobs
                 WHERE status IN ('queued', 'waiting_fulltext')
                   AND cancel_requested=0
                   AND (next_attempt_at IS NULL OR next_attempt_at<=?)
                 ORDER BY CASE status WHEN 'queued' THEN 0 ELSE 1 END, created_at
                 LIMIT 1
                """,
                (now,),
            ).fetchone()
            if row is None:
                db.commit()
                return None
            changed = db.execute(
                """UPDATE jobs SET status='processing', attempts=attempts+1,
                   error_code=NULL, message='处理中', updated_at=?
                   WHERE id=? AND status=?""",
                (now, row["id"], row["status"]),
            ).rowcount
            if changed != 1:
                db.rollback()
                return None
            self._event(db, row["id"], "processing", None, "处理中")
            claimed = db.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone()
            db.commit()
            return self._record(claimed)

    def set_fingerprint(self, job_id: str, fingerprint: str) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE jobs SET fingerprint=?, updated_at=? WHERE id=?",
                (fingerprint, utc_now(), job_id),
            )

    def complete(self, job_id: str, result: dict) -> None:
        now = utc_now()
        encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """UPDATE jobs SET status='completed', result_json=?, message='完成',
                   error_code=NULL, next_attempt_at=NULL, updated_at=? WHERE id=?""",
                (encoded, now, job_id),
            )
            self._event(db, job_id, "completed", None, "完成")
            db.commit()

    def defer(
        self,
        job_id: str,
        *,
        status: str,
        code: str,
        message: str,
        delay_seconds: int | None = None,
    ) -> None:
        next_time = None
        if delay_seconds is not None:
            next_time = (datetime.now(UTC) + timedelta(seconds=delay_seconds)).isoformat()
        now = utc_now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """UPDATE jobs SET status=?, error_code=?, message=?, next_attempt_at=?,
                   updated_at=? WHERE id=?""",
                (status, code, message, next_time, now, job_id),
            )
            self._event(db, job_id, status, code, message)
            db.commit()

    def fail_or_retry(self, job_id: str, *, code: str, message: str, retryable: bool) -> str:
        with self.connect() as db:
            row = db.execute(
                "SELECT attempts, max_attempts FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
        if row is None:
            return "failed"
        if retryable and row["attempts"] < row["max_attempts"]:
            delay = min(120, 2 ** max(1, row["attempts"]))
            self.defer(job_id, status="queued", code=code, message=message, delay_seconds=delay)
            return "queued"
        self.defer(job_id, status="failed", code=code, message=message)
        return "failed"

    def request_cancel(self, job_id: str) -> JobRecord | None:
        now = utc_now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                db.rollback()
                return None
            terminal = row["status"] in {"completed", "failed", "cancelled"}
            status = row["status"] if terminal else "cancelled"
            db.execute(
                "UPDATE jobs SET cancel_requested=1, status=?, message=?, updated_at=? WHERE id=?",
                (status, "已取消" if not terminal else None, now, job_id),
            )
            if not terminal:
                self._event(db, job_id, "cancelled", "cancelled", "用户取消")
            updated = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            db.commit()
            return self._record(updated)

    def is_cancelled(self, job_id: str) -> bool:
        with self.connect() as db:
            row = db.execute(
                "SELECT cancel_requested, status FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
        return bool(row and (row["cancel_requested"] or row["status"] == "cancelled"))

    def payload(self, job_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT abstract_mode, abstract_text FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
        return dict(row) if row else None

    def resume(self, job_id: str) -> JobRecord | None:
        now = utc_now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                db.rollback()
                return None
            if row["status"] not in {"waiting_quota", "reauthorization_required", "failed"}:
                current = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
                db.commit()
                return self._record(current)
            db.execute(
                """UPDATE jobs SET status='queued', cancel_requested=0, error_code=NULL,
                   message='手动恢复排队', next_attempt_at=NULL, updated_at=? WHERE id=?""",
                (now, job_id),
            )
            self._event(db, job_id, "queued", "manual_resume", "手动恢复排队")
            current = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            db.commit()
            return self._record(current)

    def get(self, job_id: str) -> JobRecord | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._record(row) if row else None

    def recent(self, *, limit: int = 50, library_id: int | None = None) -> list[JobRecord]:
        limit = min(max(limit, 1), 200)
        with self.connect() as db:
            if library_id is None:
                rows = db.execute(
                    "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM jobs WHERE library_id=? ORDER BY created_at DESC LIMIT ?",
                    (library_id, limit),
                ).fetchall()
        return [self._record(row) for row in rows]

    def events(self, job_id: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                """SELECT status, code, message, created_at FROM job_events
                   WHERE job_id=? ORDER BY id""",
                (job_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _event(
        db: sqlite3.Connection,
        job_id: str,
        status: str,
        code: str | None,
        message: str | None,
    ) -> None:
        db.execute(
            "INSERT INTO job_events(job_id,status,code,message,created_at) VALUES(?,?,?,?,?)",
            (job_id, status, code, message, utc_now()),
        )
    @staticmethod
    def _record(row: sqlite3.Row) -> JobRecord:
        result = json.loads(row["result_json"]) if row["result_json"] else None
        return JobRecord(
            id=row["id"],
            status=row["status"],
            library_id=row["library_id"],
            parent_key=row["parent_key"],
            attachment_key=row["attachment_key"],
            attachment_path=row["attachment_path"],
            title=row["title"],
            fingerprint=row["fingerprint"],
            instruction_version=row["instruction_version"],
            model=row["model"],
            auth_mode=row["auth_mode"],
            attempts=row["attempts"],
            max_attempts=row["max_attempts"],
            force_generation=bool(row["force_generation"]),
            cancel_requested=bool(row["cancel_requested"]),
            error_code=row["error_code"],
            message=row["message"],
            result=result,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
