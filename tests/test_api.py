"""Tests for the upscale API endpoints."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from upscale_api.config import Settings
from upscale_api.db import Database
from upscale_api.schemas import JobStatus


def test_health(client: TestClient) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


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


def test_upscale_accepts_webhook_url(
    client: TestClient, settings: Settings
) -> None:
    resp = client.post(
        "/api/upscale",
        json={
            "image_url": "https://example.com/image.jpg",
            "webhook_url": "https://client.example.com/hook",
        },
    )
    assert resp.status_code == 202
    job_id = resp.json()["id"]

    import asyncio

    from upscale_api.db import Database

    async def fetch() -> str | None:
        return (await Database(settings).get_job(job_id)).webhook_url

    assert asyncio.run(fetch()) == "https://client.example.com/hook"


def test_upscale_rejects_invalid_webhook_url(client: TestClient) -> None:
    resp = client.post(
        "/api/upscale",
        json={"image_url": "https://example.com/image.jpg", "webhook_url": "nope"},
    )
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
