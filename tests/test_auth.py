"""Tests for optional API token authentication."""

from __future__ import annotations

from fastapi.testclient import TestClient

BODY = {"image_url": "https://example.com/image.jpg"}


def test_open_api_needs_no_token(client: TestClient) -> None:
    # Default fixture has no api_token => auth disabled.
    assert client.post("/api/upscale", json=BODY).status_code == 202


def test_missing_token_is_rejected(auth_client: TestClient) -> None:
    resp = auth_client.post("/api/upscale", json=BODY)
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"


def test_wrong_token_is_rejected(auth_client: TestClient) -> None:
    resp = auth_client.post(
        "/api/upscale", json=BODY, headers={"Authorization": "Bearer nope"}
    )
    assert resp.status_code == 401


def test_correct_token_is_accepted(
    auth_client: TestClient, auth_token: str
) -> None:
    resp = auth_client.post(
        "/api/upscale",
        json=BODY,
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 202


def test_read_endpoints_require_token(
    auth_client: TestClient, auth_token: str
) -> None:
    auth = {"Authorization": f"Bearer {auth_token}"}
    job_id = auth_client.post("/api/upscale", json=BODY, headers=auth).json()["id"]

    assert auth_client.get(f"/api/jobs/{job_id}").status_code == 401
    assert auth_client.get(f"/api/jobs/{job_id}", headers=auth).status_code == 200


def test_health_is_always_open(auth_client: TestClient) -> None:
    # Health must work without a token (for monitoring/healthchecks).
    assert auth_client.get("/api/health").status_code == 200
