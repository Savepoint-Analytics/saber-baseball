"""Bronze storage layout: bucket prefixing and the local/MinIO switch.

The bucket and the Postgres database are both shared across Savepoint verticals, so
these tests pin the namespacing. Getting it wrong does not raise — it silently writes
baseball's data on top of another vertical's.
"""

from __future__ import annotations

from pathlib import Path

from src.config import Settings
from src.warehouse import sync_minio


def _settings(**env) -> Settings:
    base = {
        "SABER_STORAGE_BACKEND": "minio",
        "MINIO_BUCKET": "sabermetrics",
        "MINIO_PREFIX": "baseball",
    }
    return Settings(_env_file=None, **{**base, **env})  # type: ignore[arg-type]


def test_minio_bronze_root_is_namespaced_by_prefix():
    """The bucket is shared (soccer/ already exists); baseball must not write to root."""
    assert _settings().bronze_root == "s3://sabermetrics/baseball"


def test_prefix_is_optional():
    """A bucket owned solely by baseball needs no prefix."""
    assert _settings(MINIO_PREFIX="").bronze_root == "s3://sabermetrics"


def test_prefix_slashes_are_normalised():
    """A stray slash in .env must not produce s3://bucket//key."""
    assert _settings(MINIO_PREFIX="/baseball/").bronze_root == "s3://sabermetrics/baseball"


def test_local_backend_ignores_bucket_and_prefix():
    root = _settings(SABER_STORAGE_BACKEND="local", SABER_DATA_DIR="/tmp/x").bronze_root
    assert root.endswith("/tmp/x/bronze")
    assert "sabermetrics" not in root


def test_object_key_mirrors_the_local_tree_under_the_prefix(monkeypatch):
    monkeypatch.setattr(sync_minio.settings, "minio_prefix", "baseball")
    root = Path("/data/bronze")
    key = sync_minio.object_key(root / "statcast/season=2025/date=2025-06-24.parquet", root)
    assert key == "baseball/statcast/season=2025/date=2025-06-24.parquet"


def test_object_key_without_prefix_has_no_leading_slash(monkeypatch):
    monkeypatch.setattr(sync_minio.settings, "minio_prefix", "")
    root = Path("/data/bronze")
    assert sync_minio.object_key(root / "lahman/x.parquet", root) == "lahman/x.parquet"
