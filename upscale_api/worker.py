"""arq worker: consumes upscale jobs from Redis and runs Real-ESRGAN."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx

from .config import Settings, get_settings
from .db import Database
from .queue import UPSCALE_TASK, redis_settings
from .schemas import JobStatus


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
    try:
        input_path = settings.data_dir / "inputs" / f"{job_id}{_ext(job.image_url)}"
        output_path = settings.output_dir / f"{job_id}.png"

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


class WorkerSettings:
    """arq worker configuration. Run with: arq upscale_api.worker.WorkerSettings"""

    functions = [run_upscale]
    on_startup = startup
    redis_settings = redis_settings(get_settings())
    max_jobs = 1  # one GPU job at a time per worker instance
    job_timeout = 600
