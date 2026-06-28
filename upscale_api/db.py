"""SQLite persistence for job metadata (async, via aiosqlite)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

import aiosqlite

from .config import Settings
from .schemas import JobStatus

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    status      TEXT NOT NULL,
    image_url   TEXT NOT NULL,
    scale       INTEGER NOT NULL,
    model       TEXT NOT NULL,
    result_path TEXT,
    error       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""


@dataclass
class Job:
    """A persisted job row."""

    id: str
    status: str
    image_url: str
    scale: int
    model: str
    result_path: Optional[str]
    error: Optional[str]
    created_at: datetime
    updated_at: datetime


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_job(row: aiosqlite.Row) -> Job:
    return Job(
        id=row["id"],
        status=row["status"],
        image_url=row["image_url"],
        scale=row["scale"],
        model=row["model"],
        result_path=row["result_path"],
        error=row["error"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


class Database:
    """Thin async wrapper around a single SQLite file."""

    def __init__(self, settings: Settings) -> None:
        self._path = str(settings.database_path)

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[aiosqlite.Connection]:
        conn = await aiosqlite.connect(self._path)
        try:
            conn.row_factory = aiosqlite.Row
            # WAL lets the API and worker processes read/write concurrently.
            await conn.execute("PRAGMA journal_mode=WAL;")
            await conn.execute("PRAGMA busy_timeout=5000;")
            yield conn
        finally:
            await conn.close()

    async def init(self) -> None:
        async with self.connect() as conn:
            await conn.executescript(_SCHEMA)
            await conn.commit()

    async def create_job(
        self, *, job_id: str, image_url: str, scale: int, model: str
    ) -> Job:
        now = _now()
        async with self.connect() as conn:
            await conn.execute(
                """
                INSERT INTO jobs (
                    id, status, image_url, scale, model,
                    result_path, error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, ?)
                """,
                (job_id, JobStatus.queued.value, image_url, scale, model, now, now),
            )
            await conn.commit()
        job = await self.get_job(job_id)
        assert job is not None  # just inserted
        return job

    async def get_job(self, job_id: str) -> Optional[Job]:
        async with self.connect() as conn:
            async with conn.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ) as cur:
                row = await cur.fetchone()
        return _row_to_job(row) if row else None

    async def count_ahead(self, job: Job) -> int:
        """Number of unfinished jobs queued/processing before `job` (FIFO)."""
        async with self.connect() as conn:
            async with conn.execute(
                """
                SELECT COUNT(*) AS n FROM jobs
                WHERE status IN (?, ?) AND created_at < ?
                """,
                (
                    JobStatus.queued.value,
                    JobStatus.processing.value,
                    job.created_at.isoformat(),
                ),
            ) as cur:
                row = await cur.fetchone()
        return int(row["n"])

    async def update_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        result_path: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        async with self.connect() as conn:
            await conn.execute(
                """
                UPDATE jobs
                SET status = ?,
                    result_path = COALESCE(?, result_path),
                    error = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (status.value, result_path, error, _now(), job_id),
            )
            await conn.commit()

    async def delete_finished_before(self, cutoff: datetime) -> list[Job]:
        """Delete finished (succeeded/failed) jobs updated before `cutoff`.

        Returns the deleted rows so the caller can remove their files.
        """
        cutoff_iso = cutoff.isoformat()
        async with self.connect() as conn:
            async with conn.execute(
                """
                SELECT * FROM jobs
                WHERE status IN (?, ?) AND updated_at < ?
                """,
                (JobStatus.succeeded.value, JobStatus.failed.value, cutoff_iso),
            ) as cur:
                rows = await cur.fetchall()
            jobs = [_row_to_job(row) for row in rows]
            if jobs:
                await conn.executemany(
                    "DELETE FROM jobs WHERE id = ?", [(job.id,) for job in jobs]
                )
                await conn.commit()
        return jobs
