"""The DuckDB → Postgres publish step (no live database required)."""

from pathlib import Path

import pytest

from src.config import settings
from src.warehouse import load_postgres


def test_postgres_dsn_is_libpq_not_sqlalchemy():
    """DuckDB's postgres extension speaks libpq and rejects the +psycopg dialect."""
    dsn = settings.postgres_dsn
    assert dsn.startswith("postgresql://")
    assert "+psycopg" not in dsn
    # The SQLAlchemy URL keeps its dialect suffix; the two must not be conflated.
    assert "+psycopg" in settings.postgres_url


def test_only_curated_layers_are_published():
    """Bronze is raw licensed third-party data and must never reach the warehouse."""
    assert set(load_postgres.PUBLISHED) == {"silver", "gold"}
    assert "bronze" not in load_postgres.PUBLISHED


def test_publish_fails_clearly_when_lake_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(load_postgres, "duckdb_path", lambda: tmp_path / "absent.duckdb")
    with pytest.raises(FileNotFoundError, match="make dbt-duckdb"):
        load_postgres.publish()


def test_duckdb_path_sits_under_the_configured_data_dir():
    path = load_postgres.duckdb_path()
    assert path.name == "saber.duckdb"
    assert Path(settings.data_dir).resolve() in path.parents
