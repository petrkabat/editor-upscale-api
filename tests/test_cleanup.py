"""Tests for input deletion and the TTL cleanup cron task."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

from upscale_api.config import Settings
from upscale_api.db import Database
from upscale_api.schemas import JobStatus
from upscale_api.worker import cleanup_old_results


def _fake_redis(*, in_queue: bool = True) -> AsyncMock:
    pool = AsyncMock()
    pool.zscore = AsyncMock(return_value=1.0 if in_queue else None)
    return pool


def _backdate(
    settings: Settings, job_id: str, *, hours: int = 0, minutes: int = 0
) -> None:
    """Move a job's updated_at into the past via direct SQLite access."""
    ts = (
        datetime.now(timezone.utc) - timedelta(hours=hours, minutes=minutes)
    ).isoformat()
    conn = sqlite3.connect(str(settings.database_path))
    conn.execute("UPDATE jobs SET updated_at = ? WHERE id = ?", (ts, job_id))
    conn.commit()
    conn.close()


async def _seed_finished_job(settings: Settings, job_id: str) -> Path:
    settings.ensure_dirs()
    db = Database(settings)
    await db.init()
    await db.create_job(
        job_id=job_id,
        image_url="https://example.com/x.jpg",
        scale=4,
        model="realesrgan-x4plus",
    )
    out = settings.output_dir / f"{job_id}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"fake-png")
    await db.update_status(job_id, JobStatus.succeeded, result_path=str(out))
    return out


def test_cleanup_removes_old_jobs(settings: Settings) -> None:
    async def run() -> None:
        out = await _seed_finished_job(settings, "old-job")
        _backdate(settings, "old-job", hours=48)

        db = Database(settings)
        removed = await cleanup_old_results(
            {"db": db, "settings": settings, "redis": _fake_redis()}
        )

        assert removed == 1
        assert await db.get_job("old-job") is None
        assert not out.exists()

    asyncio.run(run())


def test_cleanup_keeps_recent_jobs(settings: Settings) -> None:
    async def run() -> None:
        out = await _seed_finished_job(settings, "fresh-job")  # updated_at = now

        db = Database(settings)
        removed = await cleanup_old_results({"db": db, "settings": settings, "redis": _fake_redis()})

        assert removed == 0
        assert await db.get_job("fresh-job") is not None
        assert out.exists()

    asyncio.run(run())


def test_cleanup_disabled_when_ttl_zero(settings: Settings) -> None:
    async def run() -> None:
        settings.result_ttl_hours = 0
        await _seed_finished_job(settings, "any-job")
        _backdate(settings, "any-job", hours=1000)

        db = Database(settings)
        removed = await cleanup_old_results({"db": db, "settings": settings, "redis": _fake_redis()})

        assert removed == 0
        assert await db.get_job("any-job") is not None

    asyncio.run(run())


async def _seed_queued_job(settings: Settings, job_id: str) -> Database:
    settings.ensure_dirs()
    db = Database(settings)
    await db.init()
    await db.create_job(
        job_id=job_id,
        image_url="https://example.com/x.jpg",
        scale=4,
        model="realesrgan-x4plus",
    )
    return db


def test_cleanup_marks_lost_jobs_failed(settings: Settings) -> None:
    async def run() -> None:
        db = await _seed_queued_job(settings, "lost-job")
        _backdate(settings, "lost-job", minutes=5)

        await cleanup_old_results(
            {"db": db, "settings": settings, "redis": _fake_redis(in_queue=False)}
        )

        job = await db.get_job("lost-job")
        assert job is not None
        assert job.status == JobStatus.failed.value
        assert job.error and "lost from queue" in job.error

    asyncio.run(run())


def test_cleanup_keeps_jobs_still_in_queue(settings: Settings) -> None:
    async def run() -> None:
        db = await _seed_queued_job(settings, "waiting-job")
        _backdate(settings, "waiting-job", hours=3)

        await cleanup_old_results(
            {"db": db, "settings": settings, "redis": _fake_redis(in_queue=True)}
        )

        job = await db.get_job("waiting-job")
        assert job is not None
        assert job.status == JobStatus.queued.value

    asyncio.run(run())


def test_cleanup_sweeps_lost_jobs_even_when_ttl_disabled(settings: Settings) -> None:
    async def run() -> None:
        settings.result_ttl_hours = 0
        db = await _seed_queued_job(settings, "lost-job")
        _backdate(settings, "lost-job", minutes=5)

        await cleanup_old_results(
            {"db": db, "settings": settings, "redis": _fake_redis(in_queue=False)}
        )

        job = await db.get_job("lost-job")
        assert job is not None
        assert job.status == JobStatus.failed.value

    asyncio.run(run())
