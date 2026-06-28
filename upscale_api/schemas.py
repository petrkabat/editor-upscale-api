"""Pydantic request/response schemas."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, HttpUrl, field_validator

from .models import SUPPORTED_MODELS, SUPPORTED_SCALES


class JobStatus(str, Enum):
    """Lifecycle states of an upscale job."""

    queued = "queued"
    processing = "processing"
    succeeded = "succeeded"
    failed = "failed"


class UpscaleRequest(BaseModel):
    """Body of POST /api/upscale."""

    image_url: HttpUrl
    scale: int = Field(default=4)
    model: str = Field(default="realesrgan-x4plus")

    @field_validator("scale")
    @classmethod
    def _validate_scale(cls, value: int) -> int:
        if value not in SUPPORTED_SCALES:
            raise ValueError(
                f"scale must be one of {list(SUPPORTED_SCALES)}"
            )
        return value

    @field_validator("model")
    @classmethod
    def _validate_model(cls, value: str) -> str:
        if value not in SUPPORTED_MODELS:
            raise ValueError(
                f"model must be one of {list(SUPPORTED_MODELS)}"
            )
        return value


class UpscaleAccepted(BaseModel):
    """Immediate response returned by POST /api/upscale."""

    id: str
    status: JobStatus


class JobResponse(BaseModel):
    """Response body of GET /api/jobs/{id}."""

    id: str
    status: JobStatus
    scale: int
    model: str
    image_url: str
    result: Optional[str] = None
    error: Optional[str] = None
    # Jobs ahead in the queue (only while status == "queued"; 0 = next up).
    queue_position: Optional[int] = None
    created_at: datetime
    updated_at: datetime
