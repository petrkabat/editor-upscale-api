"""FastAPI application exposing the upscale API under /api."""

from __future__ import annotations

import hmac
import mimetypes
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse

from .config import Settings, get_settings
from .db import Database, Job
from .queue import create_redis_pool, enqueue_upscale, read_worker_health
from .schemas import (
    HealthResponse,
    JobResponse,
    JobStatus,
    QueueHealth,
    UpscaleAccepted,
    UpscaleRequest,
    WorkerHealth,
)
from .urls import result_url


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialise storage and the Redis pool on startup."""
    settings: Settings = app.state.settings
    settings.ensure_dirs()

    db = Database(settings)
    await db.init()
    app.state.db = db

    app.state.redis_pool = await create_redis_pool(settings)
    try:
        yield
    finally:
        pool = getattr(app.state, "redis_pool", None)
        if pool is not None:
            await pool.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Application factory."""
    settings = settings or get_settings()
    app = FastAPI(title="Real-ESRGAN Upscale API", lifespan=lifespan)
    app.state.settings = settings

    def get_db() -> Database:
        return app.state.db

    def require_auth(authorization: Optional[str] = Header(default=None)) -> None:
        """Require `Authorization: Bearer <api_token>` when a token is set."""
        if not settings.api_token:
            return  # authentication disabled
        expected = f"Bearer {settings.api_token}"
        if authorization is None or not hmac.compare_digest(authorization, expected):
            raise HTTPException(
                status_code=401,
                detail="invalid or missing API token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    def to_response(job: Job, queue_position: int | None = None) -> JobResponse:
        return JobResponse(
            id=job.id,
            status=JobStatus(job.status),
            scale=job.scale,
            model=job.model,
            image_url=job.image_url,
            result=result_url(settings, job.id)
            if job.status == JobStatus.succeeded.value
            else None,
            error=job.error,
            queue_position=queue_position,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )

    @app.get("/api/health", response_model=HealthResponse)
    async def health(request: Request, db: Database = Depends(get_db)) -> HealthResponse:
        """Liveness of the API plus Redis reachability and worker heartbeat.

        Always returns 200 so tunnel/uptime checks still see the API itself;
        look at `status` / `workers.alive` to tell whether jobs will actually
        get processed.
        """
        redis_ok = True
        heartbeat = None
        try:
            heartbeat = await read_worker_health(request.app.state.redis_pool)
        except Exception:  # noqa: BLE001 - health must never raise
            redis_ok = False

        workers = (
            WorkerHealth(alive=True, **heartbeat)
            if heartbeat
            else WorkerHealth(alive=False)
        )
        counts = await db.count_by_status()
        queue = QueueHealth(
            queued=counts.get(JobStatus.queued.value, 0),
            processing=counts.get(JobStatus.processing.value, 0),
        )
        return HealthResponse(
            status="ok" if redis_ok and workers.alive else "degraded",
            redis=redis_ok,
            workers=workers,
            queue=queue,
        )

    @app.post(
        "/api/upscale",
        response_model=UpscaleAccepted,
        status_code=202,
        dependencies=[Depends(require_auth)],
    )
    async def upscale(
        payload: UpscaleRequest,
        request: Request,
        db: Database = Depends(get_db),
    ) -> UpscaleAccepted:
        job_id = uuid.uuid4().hex
        await db.create_job(
            job_id=job_id,
            image_url=str(payload.image_url),
            scale=payload.scale,
            model=payload.model,
        )
        await enqueue_upscale(request.app.state.redis_pool, job_id)
        return UpscaleAccepted(id=job_id, status=JobStatus.queued)

    @app.get(
        "/api/jobs/{job_id}",
        response_model=JobResponse,
        dependencies=[Depends(require_auth)],
    )
    async def get_job(
        job_id: str, db: Database = Depends(get_db)
    ) -> JobResponse:
        job = await db.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        position = (
            await db.count_ahead(job)
            if job.status == JobStatus.queued.value
            else None
        )
        return to_response(job, queue_position=position)

    @app.get("/api/jobs/{job_id}/result", dependencies=[Depends(require_auth)])
    async def get_result(
        job_id: str, db: Database = Depends(get_db)
    ) -> FileResponse:
        job = await db.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        if job.status != JobStatus.succeeded.value or not job.result_path:
            raise HTTPException(status_code=409, detail="result not ready")

        path = Path(job.result_path)
        if not path.is_file():
            raise HTTPException(status_code=410, detail="result file missing")

        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return FileResponse(path, media_type=media_type, filename=path.name)

    return app


app = create_app()
