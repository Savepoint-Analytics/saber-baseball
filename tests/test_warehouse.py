"""The DuckDB → Postgres publish step (no live database required)."""

from pathlib import Path

import pytest

from src.config import Settings, settings
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


def test_schema_setting_accepts_the_truncated_spelling(monkeypatch):
    """Deployed .env files carry POSTGRES_SC; reading nothing is the failure mode."""
    monkeypatch.delenv("POSTGRES_SCHEMA", raising=False)
    monkeypatch.setenv("POSTGRES_SC", "baseball_alt")
    assert Settings(_env_file=None).postgres_schema == "baseball_alt"


def test_schema_setting_prefers_the_cross_vertical_name(monkeypatch):
    """POSTGRES_SCHEMA is what the tennis vertical uses, so it must win."""
    monkeypatch.setenv("POSTGRES_SCHEMA", "baseball")
    monkeypatch.setenv("POSTGRES_SC", "ignored")
    assert Settings(_env_file=None).postgres_schema == "baseball"


def test_publish_targets_the_vertical_schema_not_bare_layer_schemas(monkeypatch, tmp_path):
    """Every vertical shares one `sports` DB, so marts must be schema-namespaced.

    Publishing to bare `silver`/`gold` would collide with the next vertical that
    did the same, which is the bug this test pins shut.
    """
    import duckdb

    lake = tmp_path / "saber.duckdb"
    con = duckdb.connect(str(lake))
    con.execute("create schema main_silver; create schema main_gold")
    con.execute("create table main_silver.sv_games as select 1 as game_pk")
    con.execute("create table main_gold.gd_team_season_record as select 1 as season")
    con.close()

    monkeypatch.setattr(load_postgres, "duckdb_path", lambda: lake)
    monkeypatch.setattr(load_postgres.settings, "postgres_schema", "baseball")

    counts = load_postgres.publish(dry_run=True)

    assert set(counts) == {"baseball.sv_games", "baseball.gd_team_season_record"}
    assert not any(k.startswith(("silver.", "gold.")) for k in counts)


def test_publish_fails_clearly_when_lake_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(load_postgres, "duckdb_path", lambda: tmp_path / "absent.duckdb")
    with pytest.raises(FileNotFoundError, match="make dbt-duckdb"):
        load_postgres.publish()


def test_duckdb_path_sits_under_the_configured_data_dir():
    path = load_postgres.duckdb_path()
    assert path.name == "saber.duckdb"
    assert Path(settings.data_dir).resolve() in path.parents
