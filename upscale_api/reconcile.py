"""Detect jobs the DB thinks are unfinished but the queue no longer holds.

The DB row is updated by the worker at the start and end of a job. If a worker
dies mid-job, the job hits arq's timeout, or the queue entry expires, arq drops
the job from Redis but nobody updates the row: it stays `queued`/`processing`
forever, clients poll indefinitely and queue counts drift. These helpers close
that gap by marking such rows `failed`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from arq.connections import ArqRedis

from .db import Database, Job
from .queue import in_queue
from .schemas import JobStatus

LOST_JOB_ERROR = "lost from queue (worker died, job timed out, or queue entry expired)"

# A job younger than this is never declared lost: the API inserts the DB row
# before enqueueing, so a brand-new row may legitimately not be in Redis yet.
LOST_GRACE = timedelta(seconds=60)

_UNFINISHED = {JobStatus.queued.value, JobStatus.processing.value}


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def reconcile_job(
    db: Database, pool: ArqRedis, job: Job, *, now: Optional[datetime] = None
) -> Job:
    """Mark `job` failed if it is unfinished in the DB but gone from the queue.

    Returns the (possibly updated) job.
    """
    if job.status not in _UNFINISHED:
        return job
    if (now or _now()) - job.updated_at < LOST_GRACE:
        return job
    if await in_queue(pool, job.id):
        return job
    await db.update_status(job.id, JobStatus.failed, error=LOST_JOB_ERROR)
    return await db.get_job(job.id) or job


async def reconcile_stale_jobs(
    db: Database, pool: ArqRedis, *, now: Optional[datetime] = None
) -> int:
    """Sweep all unfinished rows past the grace period; return how many were lost."""
    now = now or _now()
    lost = 0
    for job in await db.list_unfinished_before(now - LOST_GRACE):
        if not await in_queue(pool, job.id):
            await db.update_status(job.id, JobStatus.failed, error=LOST_JOB_ERROR)
            lost += 1
    return lost
