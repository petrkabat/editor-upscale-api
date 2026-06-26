"""Real-ESRGAN inference wrapper.

Heavy ML dependencies (torch, realesrgan, basicsr, cv2) are imported lazily so
that the API process and the test suite do not need them installed.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import Settings
from .models import MODELS, ModelSpec


def _build_arch(spec: ModelSpec) -> Any:
    """Instantiate the network architecture for a model spec."""
    from basicsr.archs.rrdbnet_arch import RRDBNet
    from realesrgan.archs.srvgg_arch import SRVGGNetCompact

    if spec.arch == "rrdbnet":
        return RRDBNet(
            num_in_ch=3, num_out_ch=3, num_feat=64,
            num_block=23, num_grow_ch=32, scale=spec.netscale,
        )
    if spec.arch == "rrdbnet6":
        return RRDBNet(
            num_in_ch=3, num_out_ch=3, num_feat=64,
            num_block=6, num_grow_ch=32, scale=spec.netscale,
        )
    if spec.arch == "srvgg":
        return SRVGGNetCompact(
            num_in_ch=3, num_out_ch=3, num_feat=64,
            num_conv=32, upscale=spec.netscale, act_type="prelu",
        )
    raise ValueError(f"unknown architecture: {spec.arch}")


@lru_cache(maxsize=4)
def _get_upscaler(model_key: str, settings_id: int) -> Any:
    """Build and cache a RealESRGANer for a given model.

    `settings_id` keys the cache to a Settings instance without making the
    object itself hashable.
    """
    from realesrgan import RealESRGANer

    settings = _SETTINGS_BY_ID[settings_id]
    spec = MODELS[model_key]
    weights_path = settings.weights_dir / f"{spec.name}.pth"

    return RealESRGANer(
        scale=spec.netscale,
        model_path=str(weights_path) if weights_path.is_file() else spec.url,
        model=_build_arch(spec),
        tile=settings.tile_size,
        tile_pad=10,
        pre_pad=0,
        half=settings.use_gpu,
        gpu_id=0 if settings.use_gpu else None,
    )


_SETTINGS_BY_ID: dict[int, Settings] = {}


def upscale_image(
    input_path: Path, output_path: Path, *, model: str, scale: int, settings: Settings
) -> Path:
    """Upscale `input_path` into `output_path` and return the output path."""
    import cv2

    _SETTINGS_BY_ID[id(settings)] = settings
    upscaler = _get_upscaler(model, id(settings))

    img = cv2.imread(str(input_path), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError(f"could not read image: {input_path}")

    output, _ = upscaler.enhance(img, outscale=scale)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output_path), output):
        raise RuntimeError(f"could not write output image: {output_path}")
    return output_path
