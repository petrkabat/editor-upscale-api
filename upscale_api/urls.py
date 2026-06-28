"""Helpers for building result URLs."""

from __future__ import annotations

from .config import Settings


def result_url(settings: Settings, job_id: str) -> str:
    """URL of a job's result image.

    Absolute when PUBLIC_BASE_URL is set, otherwise a relative API path.
    """
    path = f"/api/jobs/{job_id}/result"
    base = settings.public_base_url.rstrip("/")
    return f"{base}{path}" if base else path
