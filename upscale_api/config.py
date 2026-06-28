"""Application configuration, loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration shared by the API and the worker."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Storage
    data_dir: Path = Field(default=Path("data"))
    database_path: Path = Field(default=Path("data/jobs.db"))
    output_dir: Path = Field(default=Path("data/outputs"))

    # Redis / arq
    redis_host: str = Field(default="redis")
    redis_port: int = Field(default=6379)
    redis_database: int = Field(default=0)
    queue_name: str = Field(default="upscale:queue")

    # Worker / Real-ESRGAN
    default_model: str = Field(default="realesrgan-x4plus")
    weights_dir: Path = Field(default=Path("data/weights"))
    use_gpu: bool = Field(default=True)
    tile_size: int = Field(default=0)
    download_timeout_seconds: int = Field(default=60)
    max_image_bytes: int = Field(default=50 * 1024 * 1024)

    # Cleanup
    # Delete the downloaded input image once a job finishes.
    delete_input_after: bool = Field(default=True)
    # Remove finished jobs (DB rows + result files) older than this. 0 disables.
    result_ttl_hours: int = Field(default=24)

    # Webhooks (WaveSpeed-style, signed with the Standard Webhooks scheme).
    # Shared secret used to sign deliveries (HMAC key, "whsec_" prefix stripped).
    webhook_secret: str = Field(default="")
    webhook_timeout_seconds: int = Field(default=10)
    webhook_max_retries: int = Field(default=3)
    # Public base URL (e.g. https://upscale.example.com) used to build absolute
    # result links in webhook payloads. Empty => relative "/api/jobs/.../result".
    public_base_url: str = Field(default="")

    def ensure_dirs(self) -> None:
        """Create the directories the app writes to."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.weights_dir.mkdir(parents=True, exist_ok=True)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
