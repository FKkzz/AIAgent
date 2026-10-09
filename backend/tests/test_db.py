from pathlib import Path

from zotero_quick_read.db import QueueDB
from zotero_quick_read.schemas import JobSubmit


def request(path: Path, *, force: bool = False) -> JobSubmit:
    return JobSubmit(
        library_id=1,
        parent_key="ABCDEFGH",
        attachment_key="HGFEDCBA",
        attachment_path=str(path),
        title="A paper",
        force=force,
    )


def enqueue(db: QueueDB, item: JobSubmit):
    return db.enqueue(
        item,
        instruction_version="physics-zh-v1",
        model="model-from-account",
        auth_mode="chatgpt",
        max_attempts=3,
        fingerprint="a" * 64,
    )


def test_deduplicates_non_forced_job(tmp_path: Path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-placeholder")
    db = QueueDB(tmp_path / "queue.db")
    first, created1 = enqueue(db, request(pdf))
    second, created2 = enqueue(db, request(pdf))
    assert created1 is True
    assert created2 is False
    assert first.id == second.id


def test_force_creates_new_job(tmp_path: Path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-placeholder")
    db = QueueDB(tmp_path / "queue.db")
    first, _ = enqueue(db, request(pdf, force=True))
    second, _ = enqueue(db, request(pdf, force=True))
    assert first.id != second.id


def test_restart_recovers_processing_job(tmp_path: Path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-placeholder")
    db = QueueDB(tmp_path / "queue.db")
    queued, _ = enqueue(db, request(pdf))
    claimed = db.claim_next()
    assert claimed and claimed.id == queued.id
    assert db.get(queued.id).status == "processing"
    assert db.recover_interrupted() == 1
    assert db.get(queued.id).status == "queued"


def test_cancelled_job_is_not_claimed(tmp_path: Path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-placeholder")
    db = QueueDB(tmp_path / "queue.db")
    job, _ = enqueue(db, request(pdf))
    cancelled = db.request_cancel(job.id)
    assert cancelled.status == "cancelled"
    assert db.claim_next() is None
