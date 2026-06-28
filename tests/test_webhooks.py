"""Tests for webhook signing and delivery on job completion."""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from upscale_api import webhooks
from upscale_api.config import Settings
from upscale_api.db import Database
from upscale_api.schemas import JobStatus
from upscale_api.worker import _notify_webhook

SECRET = "whsec_testsecret"


def test_sign_is_stable_and_verifiable() -> None:
    body = b'{"id":"abc","status":"succeeded"}'
    ts = str(int(time.time()))
    sig = webhooks.sign(SECRET, "wh_1", ts, body)

    assert sig.startswith("v1,")
    assert webhooks.verify(SECRET, "wh_1", ts, body, sig)


def test_secret_prefix_is_stripped() -> None:
    body = b"{}"
    ts = "1700000000"
    assert webhooks.sign("whsec_abc", "id", ts, body) == webhooks.sign(
        "abc", "id", ts, body
    )


def test_verify_rejects_tampered_body() -> None:
    ts = str(int(time.time()))
    sig = webhooks.sign(SECRET, "wh_1", ts, b"original")
    assert not webhooks.verify(SECRET, "wh_1", ts, b"tampered", sig)


def test_verify_rejects_stale_timestamp() -> None:
    old_ts = str(int(time.time()) - 10_000)
    sig = webhooks.sign(SECRET, "wh_1", old_ts, b"body")
    assert not webhooks.verify(SECRET, "wh_1", old_ts, b"body", sig)


def test_notify_webhook_posts_signed_payload(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.webhook_secret = SECRET
    settings.public_base_url = "https://up.example.com"

    sent: dict[str, object] = {}

    async def fake_send(url: str, payload: bytes, **kwargs: object) -> bool:
        sent["url"] = url
        sent["payload"] = payload
        sent["secret"] = kwargs.get("secret")
        return True

    monkeypatch.setattr(webhooks, "send", fake_send)

    async def run() -> None:
        settings.ensure_dirs()
        db = Database(settings)
        await db.init()
        await db.create_job(
            job_id="job-1",
            image_url="https://example.com/x.jpg",
            scale=4,
            model="realesrgan-x4plus",
            webhook_url="https://client.example.com/hook",
        )
        await db.update_status(
            "job-1", JobStatus.succeeded, result_path="/data/outputs/job-1.png"
        )
        await _notify_webhook(db, settings, "job-1")

    asyncio.run(run())

    assert sent["url"] == "https://client.example.com/hook"
    assert sent["secret"] == SECRET
    body = json.loads(sent["payload"])  # type: ignore[arg-type]
    assert body["id"] == "job-1"
    assert body["status"] == "succeeded"
    # Absolute result URL because public_base_url is set.
    assert body["result"] == "https://up.example.com/api/jobs/job-1/result"


def test_notify_webhook_skipped_without_url(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = False

    async def fake_send(*args: object, **kwargs: object) -> bool:
        nonlocal called
        called = True
        return True

    monkeypatch.setattr(webhooks, "send", fake_send)

    async def run() -> None:
        settings.ensure_dirs()
        db = Database(settings)
        await db.init()
        await db.create_job(
            job_id="job-2",
            image_url="https://example.com/x.jpg",
            scale=4,
            model="realesrgan-x4plus",
        )
        await db.update_status("job-2", JobStatus.succeeded, result_path="/x.png")
        await _notify_webhook(db, settings, "job-2")

    asyncio.run(run())
    assert called is False
