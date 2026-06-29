"""Centralized, env-driven configuration for saber-baseball.

All settings come from environment variables (loaded from `.env` in dev). Nothing
here hard-codes a secret. Import the singleton ``settings`` everywhere rather than
reading ``os.environ`` directly, so config stays in one auditable place.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings, validated and typed."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ---- Environment --------------------------------------------------------
    env: str = Field(default="dev", alias="SABER_ENV")

    # ---- Storage ------------------------------------------------------------
    storage_backend: str = Field(default="local", alias="SABER_STORAGE_BACKEND")
    data_dir: Path = Field(default=Path("./data"), alias="SABER_DATA_DIR")

    # ---- PostgreSQL ---------------------------------------------------------
    postgres_host: str = Field(default="localhost", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")
    postgres_user: str = Field(default="saber", alias="POSTGRES_USER")
    postgres_password: str = Field(default="change-me", alias="POSTGRES_PASSWORD")
    postgres_db: str = Field(default="saber_baseball", alias="POSTGRES_DB")

    # ---- MinIO / S3 ---------------------------------------------------------
    minio_endpoint: str = Field(default="http://localhost:9000", alias="MINIO_ENDPOINT")
    minio_access_key: str = Field(default="saber", alias="MINIO_ROOT_USER")
    minio_secret_key: str = Field(default="change-me", alias="MINIO_ROOT_PASSWORD")
    minio_bucket: str = Field(default="saber-bronze", alias="MINIO_BUCKET")

    # ---- Ingestion politeness (compliance) ----------------------------------
    bbref_rpm: int = Field(default=18, alias="SABER_BBREF_RPM")
    savant_rpm: int = Field(default=30, alias="SABER_SAVANT_RPM")
    statsapi_rpm: int = Field(default=60, alias="SABER_STATSAPI_RPM")
    http_cache_ttl_days: int = Field(default=7, alias="SABER_HTTP_CACHE_TTL_DAYS")
    user_agent: str = Field(
        default="savepoint-saber-baseball/0.1 (contact: alex@savepointanalytics.com)",
        alias="SABER_USER_AGENT",
    )

    # ---- Derived helpers ----------------------------------------------------
    @property
    def postgres_url(self) -> str:
        """SQLAlchemy/psycopg connection URL for the curated warehouse."""
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def bronze_root(self) -> str:
        """Root URI for the bronze (raw) landing zone.

        ``s3://bucket`` when backend=minio, else a local ``data/bronze`` path.
        """
        if self.storage_backend == "minio":
            return f"s3://{self.minio_bucket}"
        return str((self.data_dir / "bronze").resolve())

    @property
    def cache_dir(self) -> Path:
        """On-disk HTTP cache directory."""
        return (self.data_dir / "cache").resolve()

    def s3_storage_options(self) -> dict[str, object]:
        """Storage options for fsspec/s3fs/pyarrow when targeting MinIO."""
        return {
            "key": self.minio_access_key,
            "secret": self.minio_secret_key,
            "client_kwargs": {"endpoint_url": self.minio_endpoint},
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached settings singleton."""
    return Settings()


# Convenience module-level singleton.
settings = get_settings()
