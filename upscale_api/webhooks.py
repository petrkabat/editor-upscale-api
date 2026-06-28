"""Signed webhook delivery (WaveSpeed-style, Standard Webhooks scheme).

Each delivery carries three headers:

    webhook-id         unique id of this delivery
    webhook-timestamp  unix seconds when it was sent
    webhook-signature  "v1,<base64(HMAC_SHA256)>"

The signature is computed over the string ``{id}.{timestamp}.{body}`` using the
shared secret as the HMAC key (with any ``whsec_`` prefix stripped). Receivers
recompute it over the raw body and compare in constant time.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import time
import uuid

import httpx

logger = logging.getLogger("upscale_api.webhooks")

_SECRET_PREFIX = "whsec_"


def _signing_key(secret: str) -> bytes:
    return secret.removeprefix(_SECRET_PREFIX).encode("utf-8")


def sign(secret: str, webhook_id: str, timestamp: str, body: bytes) -> str:
    """Return the `webhook-signature` header value for a delivery."""
    signed = f"{webhook_id}.{timestamp}.".encode("utf-8") + body
    digest = hmac.new(_signing_key(secret), signed, hashlib.sha256).digest()
    return "v1," + base64.b64encode(digest).decode("utf-8")


def verify(
    secret: str,
    webhook_id: str,
    timestamp: str,
    body: bytes,
    signature_header: str,
    *,
    tolerance_seconds: int = 300,
) -> bool:
    """Verify an incoming webhook (mirrors what a receiver would do)."""
    try:
        if abs(time.time() - int(timestamp)) > tolerance_seconds:
            return False
    except (TypeError, ValueError):
        return False
    expected = sign(secret, webhook_id, timestamp, body)
    # The header may carry several space-separated "v1,<sig>" values.
    for candidate in signature_header.split():
        if hmac.compare_digest(candidate, expected):
            return True
    return False


async def send(
    url: str,
    payload: bytes,
    *,
    secret: str = "",
    timeout_seconds: int = 10,
    max_retries: int = 3,
) -> bool:
    """POST `payload` to `url`, signed if a secret is set. Returns success.

    Retries on network errors and 5xx with exponential backoff. Failures are
    logged but never raised — a dead webhook must not fail the job.
    """
    webhook_id = uuid.uuid4().hex
    timestamp = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "upscale-api-webhook/1",
        "webhook-id": webhook_id,
        "webhook-timestamp": timestamp,
    }
    if secret:
        headers["webhook-signature"] = sign(secret, webhook_id, timestamp, payload)

    last_error: str = ""
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        for attempt in range(1, max_retries + 1):
            try:
                resp = await client.post(url, content=payload, headers=headers)
                if resp.status_code < 500:
                    if resp.is_success:
                        return True
                    last_error = f"HTTP {resp.status_code}"
                    break  # 4xx won't be fixed by retrying
                last_error = f"HTTP {resp.status_code}"
            except httpx.HTTPError as exc:
                last_error = str(exc)

            if attempt < max_retries:
                await asyncio.sleep(2 ** (attempt - 1))

    logger.warning("webhook delivery to %s failed: %s", url, last_error)
    return False
