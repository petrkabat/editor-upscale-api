"""Real-ESRGAN model registry."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelSpec:
    """Describes a Real-ESRGAN model and where to fetch its weights."""

    name: str
    netscale: int
    url: str
    # Architecture selector consumed by the upscaler module.
    arch: str


# Supported models. Keys are the values accepted by the API `model` field.
MODELS: dict[str, ModelSpec] = {
    "realesrgan-x4plus": ModelSpec(
        name="RealESRGAN_x4plus",
        netscale=4,
        arch="rrdbnet",
        url=(
            "https://github.com/xinntao/Real-ESRGAN/releases/download/"
            "v0.1.0/RealESRGAN_x4plus.pth"
        ),
    ),
    "realesrnet-x4plus": ModelSpec(
        name="RealESRNet_x4plus",
        netscale=4,
        arch="rrdbnet",
        url=(
            "https://github.com/xinntao/Real-ESRGAN/releases/download/"
            "v0.1.1/RealESRNet_x4plus.pth"
        ),
    ),
    "realesrgan-x4plus-anime": ModelSpec(
        name="RealESRGAN_x4plus_anime_6B",
        netscale=4,
        arch="rrdbnet6",
        url=(
            "https://github.com/xinntao/Real-ESRGAN/releases/download/"
            "v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth"
        ),
    ),
    "realesr-general-x4v3": ModelSpec(
        name="realesr-general-x4v3",
        netscale=4,
        arch="srvgg",
        url=(
            "https://github.com/xinntao/Real-ESRGAN/releases/download/"
            "v0.2.5.0/realesr-general-x4v3.pth"
        ),
    ),
}

SUPPORTED_MODELS: tuple[str, ...] = tuple(MODELS.keys())
SUPPORTED_SCALES: tuple[int, ...] = (2, 3, 4)
