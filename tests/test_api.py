"""Tests for the upscale API endpoints."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from upscale_api.config import Settings
from upscale_api.db import Database
from upscale_api.schemas import JobStatus


def test_health_degraded_without_worker(client: TestClient) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["redis"] is True
    assert body["workers"]["alive"] is False
    assert body["queue"] == {"queued": 0, "processing": 0}


def test_health_reports_worker_heartbeat_and_queue(
    client: TestClient, fake_pool: AsyncMock
) -> None:
    fake_pool.get.return_value = (
        b"Aug-27 10:15:02 j_complete=12 j_failed=1 j_retried=0 j_ongoing=1 queued=3"
    )
    for _ in range(2):
        client.post("/api/upscale", json={"image_url": "https://example.com/i.jpg"})

    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["workers"] == {
        "alive": True,
        "last_seen": "Aug-27 10:15:02",
        "ongoing": 1,
        "complete": 12,
        "failed": 1,
        "retried": 0,
    }
    assert body["queue"] == {"queued": 2, "processing": 0}


def test_health_degraded_when_redis_down(
    client: TestClient, fake_pool: AsyncMock
) -> None:
    fake_pool.get.side_effect = ConnectionError("redis down")
    body = client.get("/api/health").json()
    assert body["status"] == "degraded"
    assert body["redis"] is False
    assert body["workers"]["alive"] is False


def test_upscale_creates_queued_job(
    client: TestClient, fake_pool: AsyncMock
) -> None:
    resp = client.post(
        "/api/upscale",
        json={
            "image_url": "https://example.com/image.jpg",
            "scale": 4,
            "model": "realesrgan-x4plus",
        },
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "queued"
    assert body["id"]

    # The job was pushed onto the queue with its id.
    fake_pool.enqueue_job.assert_awaited_once()
    args = fake_pool.enqueue_job.await_args
    assert args.args[0] == "run_upscale"
    assert args.args[1] == body["id"]


def test_upscale_uses_defaults(client: TestClient) -> None:
    resp = client.post(
        "/api/upscale", json={"image_url": "https://example.com/x.png"}
    )
    assert resp.status_code == 202


def test_upscale_rejects_bad_scale(client: TestClient) -> None:
    resp = client.post(
        "/api/upscale",
        json={"image_url": "https://example.com/image.jpg", "scale": 5},
    )
    assert resp.status_code == 422


def test_upscale_rejects_unknown_model(client: TestClient) -> None:
    resp = client.post(
        "/api/upscale",
        json={"image_url": "https://example.com/image.jpg", "model": "nope"},
    )
    assert resp.status_code == 422


def test_upscale_rejects_invalid_url(client: TestClient) -> None:
    resp = client.post("/api/upscale", json={"image_url": "not-a-url"})
    assert resp.status_code == 422


def test_get_job_returns_queued(client: TestClient) -> None:
    create = client.post(
        "/api/upscale", json={"image_url": "https://example.com/image.jpg"}
    )
    job_id = create.json()["id"]

    resp = client.get(f"/api/jobs/{job_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == job_id
    assert body["status"] == "queued"
    assert body["result"] is None
    # First (and only) queued job → nothing ahead of it.
    assert body["queue_position"] == 0


def test_queue_position_comes_from_live_queue(
    client: TestClient, fake_pool: AsyncMock
) -> None:
    job_id = client.post(
        "/api/upscale", json={"image_url": "https://example.com/image.jpg"}
    ).json()["id"]

    fake_pool.zrank.return_value = 5
    body = client.get(f"/api/jobs/{job_id}").json()
    assert body["queue_position"] == 5
    fake_pool.zrank.assert_awaited_with("arq:queue", job_id)


def test_queue_position_null_when_job_not_in_queue(
    client: TestClient, fake_pool: AsyncMock
) -> None:
    """A row still 'queued' in the DB but gone from Redis (lost) has no position."""
    job_id = client.post(
        "/api/upscale", json={"image_url": "https://example.com/image.jpg"}
    ).json()["id"]

    fake_pool.zrank.return_value = None
    body = client.get(f"/api/jobs/{job_id}").json()
    assert body["status"] == "queued"
    assert body["queue_position"] is None


def test_queue_position_null_when_redis_down(
    client: TestClient, fake_pool: AsyncMock
) -> None:
    job_id = client.post(
        "/api/upscale", json={"image_url": "https://example.com/image.jpg"}
    ).json()["id"]

    fake_pool.zrank.side_effect = ConnectionError("redis down")
    resp = client.get(f"/api/jobs/{job_id}")
    assert resp.status_code == 200
    assert resp.json()["queue_position"] is None


def _backdate(settings: Settings, job_id: str, *, seconds: int) -> None:
    ts = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
    conn = sqlite3.connect(str(settings.database_path))
    conn.execute("UPDATE jobs SET updated_at = ? WHERE id = ?", (ts, job_id))
    conn.commit()
    conn.close()


def _create(client: TestClient) -> str:
    return client.post(
        "/api/upscale", json={"image_url": "https://example.com/image.jpg"}
    ).json()["id"]


def test_lost_queued_job_is_marked_failed(
    client: TestClient, fake_pool: AsyncMock, settings: Settings
) -> None:
    job_id = _create(client)
    _backdate(settings, job_id, seconds=120)
    fake_pool.zscore.return_value = None  # arq no longer holds it

    body = client.get(f"/api/jobs/{job_id}").json()
    assert body["status"] == "failed"
    assert "lost from queue" in body["error"]
    assert body["queue_position"] is None


def test_lost_processing_job_is_marked_failed(
    client: TestClient, fake_pool: AsyncMock, settings: Settings
) -> None:
    job_id = _create(client)
    conn = sqlite3.connect(str(settings.database_path))
    conn.execute("UPDATE jobs SET status = 'processing' WHERE id = ?", (job_id,))
    conn.commit()
    conn.close()
    _backdate(settings, job_id, seconds=120)
    fake_pool.zscore.return_value = None

    body = client.get(f"/api/jobs/{job_id}").json()
    assert body["status"] == "failed"
    assert "lost from queue" in body["error"]


def test_fresh_job_not_in_redis_yet_stays_queued(
    client: TestClient, fake_pool: AsyncMock
) -> None:
    """Within the grace period a missing queue entry is not treated as lost."""
    job_id = _create(client)
    fake_pool.zscore.return_value = None

    body = client.get(f"/api/jobs/{job_id}").json()
    assert body["status"] == "queued"


def test_old_job_still_in_queue_stays_queued(
    client: TestClient, fake_pool: AsyncMock, settings: Settings
) -> None:
    job_id = _create(client)
    _backdate(settings, job_id, seconds=3600)
    fake_pool.zrank.return_value = 2

    body = client.get(f"/api/jobs/{job_id}").json()
    assert body["status"] == "queued"
    assert body["queue_position"] == 2


def test_get_job_not_found(client: TestClient) -> None:
    resp = client.get("/api/jobs/does-not-exist")
    assert resp.status_code == 404


def test_result_conflict_when_not_ready(client: TestClient) -> None:
    create = client.post(
        "/api/upscale", json={"image_url": "https://example.com/image.jpg"}
    )
    job_id = create.json()["id"]

    resp = client.get(f"/api/jobs/{job_id}/result")
    assert resp.status_code == 409


def test_succeeded_job_exposes_result(
    client: TestClient, settings: Settings
) -> None:
    create = client.post(
        "/api/upscale", json={"image_url": "https://example.com/image.jpg"}
    )
    job_id = create.json()["id"]

    # Simulate the worker finishing: write an output file + mark succeeded.
    out = settings.output_dir / f"{job_id}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    # Minimal valid PNG (1x1).
    out.write_bytes(_PNG_1x1)

    import asyncio

    async def finish() -> None:
        db = Database(settings)
        await db.update_status(
            job_id, JobStatus.succeeded, result_path=str(out)
        )

    asyncio.run(finish())

    job_resp = client.get(f"/api/jobs/{job_id}")
    assert job_resp.json()["result"] == f"/api/jobs/{job_id}/result"

    result = client.get(f"/api/jobs/{job_id}/result")
    assert result.status_code == 200
    assert result.headers["content-type"] == "image/png"
    assert result.content == _PNG_1x1


# A 1x1 transparent PNG.
_PNG_1x1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000100ffff03000006000557bfabd400"
    "00000049454e44ae426082"
)
