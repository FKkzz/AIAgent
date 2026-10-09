from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from . import __version__
from .errors import QuickReadError
from .local_auth import token_file_path, token_matches
from .pdf import fingerprint_file
from .runtime import Runtime, build_runtime
from .schemas import JobSubmit


def create_app(
    *,
    data_dir: Path | None = None,
    runtime: Runtime | None = None,
    start_worker: bool = True,
    allow_test_client: bool = False,
) -> FastAPI:
    active = runtime or build_runtime(data_dir)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if start_worker:
            active.worker.start()
        try:
            yield
        finally:
            if start_worker:
                active.worker.stop()

    app = FastAPI(
        title="Zotero Quick Read local API",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.runtime = active

    @app.middleware("http")
    async def loopback_boundary(request: Request, call_next):
        client_host = request.client.host if request.client else ""
        if client_host not in {"127.0.0.1", "::1"} and not (
            allow_test_client and client_host == "testclient"
        ):
            return JSONResponse(
                status_code=403,
                content={"error": {"code": "loopback_only", "message": "Loopback only."}},
            )
        host = request.headers.get("host", "").split(":", 1)[0].strip("[]").casefold()
        allowed_hosts = {"127.0.0.1", "::1"}
        if allow_test_client:
            allowed_hosts.add("testserver")
        if host not in allowed_hosts:
            return JSONResponse(
                status_code=400,
                content={"error": {"code": "invalid_host", "message": "Invalid Host header."}},
            )
        if request.headers.get("origin"):
            return JSONResponse(
                status_code=403,
                content={
                    "error": {
                        "code": "browser_origin_rejected",
                        "message": "Browser-origin requests are not accepted.",
                    }
                },
            )
        return await call_next(request)

    @app.exception_handler(QuickReadError)
    async def quick_read_error(_request: Request, exc: QuickReadError):
        return JSONResponse(
            status_code=503 if exc.retryable else 400,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "retryable": exc.retryable,
                }
            },
        )

    def require_token(authorization: str | None = Header(default=None)) -> None:
        prefix = "Bearer "
        supplied = (
            authorization[len(prefix) :]
            if authorization and authorization.startswith(prefix)
            else None
        )
        if not token_matches(active.local_token, supplied):
            raise HTTPException(
                status_code=401,
                detail={"code": "local_auth_failed", "message": "Invalid local API token."},
            )

    protected = [Depends(require_token)]

    @app.get("/health")
    def health():
        return {"status": "ok", "version": __version__, "loopback": True}

    @app.get("/api/v1/info", dependencies=protected)
    def info():
        settings = active.settings
        return {
            "version": __version__,
            "auth_mode": settings.auth_mode,
            "selected_model": settings.selected_model,
            "prompt_version": settings.output.prompt_version,
            "proxy": {
                "enabled": settings.proxy.enabled,
                "version": settings.proxy.version,
                "host": settings.proxy.host,
                "port": settings.proxy.port,
                "dns": settings.proxy.dns,
                "username_configured": bool(settings.proxy.username),
                "password_configured": bool(
                    active.secrets.get(settings.proxy.password_secret)
                ),
            },
            "token_file": str(token_file_path(active.data_dir)),
        }

    @app.post("/api/v1/jobs", dependencies=protected)
    def submit_job(payload: JobSubmit):
        fingerprint = None
        attachment = Path(payload.attachment_path)
        if (
            not payload.abstract_mode
            and attachment.is_file()
            and attachment.suffix.casefold() == ".pdf"
        ):
            fingerprint = fingerprint_file(attachment)
        model = payload.model or active.settings.selected_model
        auth_mode = payload.auth_mode or active.settings.auth_mode
        job, created = active.db.enqueue(
            payload,
            instruction_version=active.settings.output.prompt_version,
            model=model,
            auth_mode=auth_mode,
            max_attempts=active.settings.max_attempts,
            fingerprint=fingerprint,
        )
        active.worker.wake()
        return {"job": job.model_dump(mode="json"), "created": created}

    @app.get("/api/v1/jobs", dependencies=protected)
    def list_jobs(
        limit: int = Query(default=50, ge=1, le=200),
        library_id: int | None = Query(default=None, ge=0),
        status: str | None = Query(default=None),
    ):
        jobs = active.db.recent(limit=limit, library_id=library_id)
        if status:
            jobs = [job for job in jobs if job.status == status]
        return {"jobs": [job.model_dump(mode="json") for job in jobs]}

    @app.get("/api/v1/jobs/{job_id}", dependencies=protected)
    def get_job(job_id: str):
        job = active.db.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return {"job": job.model_dump(mode="json"), "events": active.db.events(job_id)}

    @app.post("/api/v1/jobs/{job_id}/cancel", dependencies=protected)
    def cancel_job(job_id: str):
        job = active.db.request_cancel(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        active.worker.wake()
        return {"job": job.model_dump(mode="json")}

    @app.post("/api/v1/jobs/{job_id}/resume", dependencies=protected)
    def resume_job(job_id: str):
        job = active.db.resume(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        active.worker.wake()
        return {"job": job.model_dump(mode="json")}

    @app.get("/api/v1/models", dependencies=protected)
    def models(auth_mode: str | None = Query(default=None)):
        return {"models": active.client.list_models(auth_mode=auth_mode)}

    @app.post("/api/v1/smoke", dependencies=protected)
    def smoke(model: str | None = Query(default=None), auth_mode: str | None = Query(default=None)):
        response = active.client.minimal_request(model=model, auth_mode=auth_mode)
        return {
            "completed": True,
            "text": response.text,
            "model": response.model,
            "response_id": response.response_id,
            "usage": response.usage,
        }

    return app
