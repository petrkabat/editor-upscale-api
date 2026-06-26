"""Test fixtures: a TestClient backed by a temp SQLite DB and a fake queue."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from upscale_api import api
from upscale_api.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    data = tmp_path / "data"
    return Settings(
        data_dir=data,
        database_path=data / "jobs.db",
        output_dir=data / "outputs",
    )


@pytest.fixture
def fake_pool() -> AsyncMock:
    """A stand-in for the arq Redis pool that records enqueued jobs."""
    pool = AsyncMock()
    pool.enqueue_job = AsyncMock()
    pool.close = AsyncMock()
    return pool


@pytest.fixture
def client(
    settings: Settings, fake_pool: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    # Replace the real Redis pool with the fake one during lifespan startup.
    monkeypatch.setattr(api, "create_redis_pool", AsyncMock(return_value=fake_pool))

    app = api.create_app(settings)
    with TestClient(app) as test_client:
        yield test_client
