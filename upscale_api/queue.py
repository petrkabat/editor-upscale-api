"""Redis / arq queue helpers shared by the API and worker."""

from __future__ import annotations

import re
from typing import Any, Optional

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from arq.constants import default_queue_name, health_check_key_suffix

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


# Key under which arq workers periodically record their health. Every worker
# instance writes the same key, so it tells "is at least one worker alive",
# not how many there are.
HEALTH_CHECK_KEY = default_queue_name + health_check_key_suffix

_HEALTH_FIELD_RE = re.compile(r"(\w+)=(\d+)")


def parse_worker_health(raw: Optional[bytes | str]) -> Optional[dict[str, Any]]:
    """Parse arq's health-check string into a dict.

    arq writes e.g.
    ``Aug-27 10:15:02 j_complete=12 j_failed=1 j_retried=0 j_ongoing=1 queued=0``.
    Returns None when no worker has reported (key missing / expired).
    """
    if not raw:
        return None
    text = raw.decode() if isinstance(raw, bytes) else raw
    fields = {k: int(v) for k, v in _HEALTH_FIELD_RE.findall(text)}
    first_field = text.find(" j_complete=")
    return {
        "last_seen": text[:first_field] if first_field > 0 else None,
        "ongoing": fields.get("j_ongoing", 0),
        "complete": fields.get("j_complete", 0),
        "failed": fields.get("j_failed", 0),
        "retried": fields.get("j_retried", 0),
    }


async def read_worker_health(pool: ArqRedis) -> Optional[dict[str, Any]]:
    """Return the latest worker heartbeat, or None if no worker is alive."""
    return parse_worker_health(await pool.get(HEALTH_CHECK_KEY))
