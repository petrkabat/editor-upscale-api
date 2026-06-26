"""Redis / arq queue helpers shared by the API and worker."""

from __future__ import annotations

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from .config import Settings

# Name of the worker task that processes upscale jobs.
UPSCALE_TASK = "run_upscale"


def redis_settings(settings: Settings) -> RedisSettings:
    """Build arq RedisSettings from app settings."""
    return RedisSettings(
        host=settings.redis_host,
        port=settings.redis_port,
        database=settings.redis_database,
    )


async def create_redis_pool(settings: Settings) -> ArqRedis:
    """Create an arq Redis pool for enqueueing jobs."""
    return await create_pool(redis_settings(settings))


async def enqueue_upscale(pool: ArqRedis, job_id: str) -> None:
    """Push an upscale job onto the queue, keyed by job id."""
    await pool.enqueue_job(UPSCALE_TASK, job_id, _job_id=job_id)
