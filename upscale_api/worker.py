"""arq worker: consumes upscale jobs from Redis and runs Real-ESRGAN."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from arq import cron

from .config import Settings, get_settings
from .db import Database
from .queue import redis_settings
from .reconcile import reconcile_stale_jobs
from .schemas import JobStatus

logger = logging.getLogger("upscale_api.worker")


async def _download_image(url: str, dest: Path, settings: Settings) -> None:
    """Download `url` to `dest`, enforcing a size limit."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    timeout = httpx.Timeout(settings.download_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            written = 0
            with dest.open("wb") as fh:
                async for chunk in resp.aiter_bytes():
                    written += len(chunk)
                    if written > settings.max_image_bytes:
                        raise ValueError("input image exceeds size limit")
                    fh.write(chunk)


async def run_upscale(ctx: dict[str, Any], job_id: str) -> str:
    """Process a single upscale job. Returns the final status."""
    db: Database = ctx["db"]
    settings: Settings = ctx["settings"]

    job = await db.get_job(job_id)
    if job is None:
        return JobStatus.failed.value

    await db.update_status(job_id, JobStatus.processing)
    input_path = settings.data_dir / "inputs" / f"{job_id}{_ext(job.image_url)}"
    output_path = settings.output_dir / f"{job_id}.png"
    try:
        await _download_image(job.image_url, input_path, settings)

        # Run the (blocking, CPU/GPU-bound) inference off the event loop.
        from .upscaler import upscale_image

        await asyncio.to_thread(
            upscale_image,
            input_path,
            output_path,
            model=job.model,
            scale=job.scale,
            settings=settings,
        )

        await db.update_status(
            job_id, JobStatus.succeeded, result_path=str(output_path)
        )
        return JobStatus.succeeded.value
    except Exception as exc:  # noqa: BLE001 - record any failure on the job
        await db.update_status(job_id, JobStatus.failed, error=str(exc))
        return JobStatus.failed.value
    finally:
        # The downloaded input is only needed during inference.
        if settings.delete_input_after:
            input_path.unlink(missing_ok=True)


async def cleanup_old_results(ctx: dict[str, Any]) -> int:
    """Cron task: mark lost jobs failed, then remove finished jobs past the TTL.

    Coordinated by arq across worker instances, so it runs once per tick.
    Returns the number of jobs removed.
    """
    db: Database = ctx["db"]
    settings: Settings = ctx["settings"]

    # Rows stuck in queued/processing whose queue entry is gone.
    lost = await reconcile_stale_jobs(db, ctx["redis"])
    if lost:
        logger.warning("cleanup marked %d lost job(s) as failed", lost)

    if settings.result_ttl_hours <= 0:
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.result_ttl_hours)
    jobs = await db.delete_finished_before(cutoff)
    for job in jobs:
        if job.result_path:
            Path(job.result_path).unlink(missing_ok=True)
        # Remove any stray input that was kept (delete_input_after disabled).
        for leftover in (settings.data_dir / "inputs").glob(f"{job.id}.*"):
            leftover.unlink(missing_ok=True)

    if jobs:
        logger.info("cleanup removed %d finished job(s) older than %s",
                    len(jobs), cutoff.isoformat())
    return len(jobs)


def _ext(url: str) -> str:
    suffix = Path(url.split("?")[0]).suffix.lower()
    return suffix if suffix in {".jpg", ".jpeg", ".png", ".webp", ".bmp"} else ".png"


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    settings.ensure_dirs()
    db = Database(settings)
    await db.init()
    ctx["settings"] = settings
    ctx["db"] = db

    # Warm the model so the first real job isn't a slow cold start.
    if settings.preload_model:
        try:
            from .upscaler import preload

            t0 = time.monotonic()
            await asyncio.to_thread(preload, settings, settings.default_model)
            logger.info(
                "preloaded model %s in %.1fs",
                settings.default_model, time.monotonic() - t0,
            )
        except Exception as exc:  # noqa: BLE001 - preload is best effort
            logger.warning("model preload failed: %s", exc)


class WorkerSettings:
    """arq worker configuration. Run with: arq upscale_api.worker.WorkerSettings"""

    functions = [run_upscale]
    # Run cleanup at the top of every hour (arq dedupes across instances).
    cron_jobs = [cron(cleanup_old_results, minute=0)]
    on_startup = startup
    redis_settings = redis_settings(get_settings())
    max_jobs = 1  # one GPU job at a time per worker instance
    job_timeout = 600
    # Refresh the `arq:queue:health-check` key every 30s (arq default is 1h) so
    # GET /api/health notices a dead worker within ~30s instead of an hour.
    health_check_interval = 30
