"""Publish modeled DuckDB tables into the PostgreSQL serving warehouse.

This is the bridge between the two halves of the storage architecture in
``CLAUDE.md`` §4. DuckDB is the *engine* over bronze Parquet; Postgres is the
*curated serving warehouse*. They do not talk to each other through dbt, because
the bronze models are ``read_parquet()`` views and ``read_parquet`` is a DuckDB
function that Postgres does not have — pointing ``dbt build`` at the postgres
target fails to compile the bronze layer and skips everything downstream.

So the split is:

  * ``dbt build --target duckdb`` — bronze → silver → gold, all transforms and
    tests run in DuckDB where the Parquet lives.
  * ``python -m src.warehouse.load_postgres`` — copy the *modeled results only*
    into Postgres, which is exactly the "push only modeled results into Postgres"
    rule from ``CLAUDE.md`` §4. Raw pitch-level data never lands in Postgres.

The copy uses DuckDB's ``postgres`` extension (ATTACH ... TYPE POSTGRES) so the
data never round-trips through Python memory.

Run::

    python -m src.warehouse.load_postgres
    python -m src.warehouse.load_postgres --dry-run
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import typer
from loguru import logger

from src.config import settings

app = typer.Typer(add_completion=False, help="Publish modeled tables to Postgres.")

# Only curated layers are published. Bronze stays in the lake: it is raw
# third-party data under source licenses (see public-data-sources.md), it is wide
# and schema-drifting, and Postgres is for serving derived analysis.
PUBLISHED: dict[str, tuple[str, ...]] = {
    "silver": ("sv_games",),
    "gold": ("gd_team_season_record",),
}


def duckdb_path() -> Path:
    """Location of the DuckDB lake file that dbt materializes into."""
    return (settings.data_dir / "saber.duckdb").resolve()


def publish(dry_run: bool = False) -> dict[str, int]:
    """Copy each published table from DuckDB into Postgres, returning row counts.

    Tables are replaced wholesale rather than merged. These are small, fully
    derived marts that dbt can rebuild from bronze at any time, so a replace keeps
    Postgres a pure projection of the lake with no drift of its own.
    """
    path = duckdb_path()
    if not path.exists():
        raise FileNotFoundError(f"No DuckDB lake at {path}. Run `make dbt-duckdb` first.")

    con = duckdb.connect(str(path))
    con.execute("INSTALL postgres; LOAD postgres;")
    con.execute(f"ATTACH '{settings.postgres_dsn}' AS pg (TYPE POSTGRES)")
    logger.info("attached postgres at {}:{}", settings.postgres_host, settings.postgres_port)

    counts: dict[str, int] = {}
    for schema, tables in PUBLISHED.items():
        if not dry_run:
            con.execute(f"CREATE SCHEMA IF NOT EXISTS pg.{schema}")
        for table in tables:
            source = f"main_{schema}.{table}"
            target = f"pg.{schema}.{table}"
            row = con.sql(f"select count(*) from {source}").fetchone()
            rows = int(row[0]) if row else 0
            counts[f"{schema}.{table}"] = rows
            if dry_run:
                logger.info("[dry-run] would publish {} rows: {} -> {}", rows, source, target)
                continue
            con.execute(f"CREATE OR REPLACE TABLE {target} AS SELECT * FROM {source}")
            logger.success("published {} rows: {} -> {}", rows, source, target)

    con.close()
    return counts


@app.command()
def main(
    dry_run: bool = typer.Option(False, "--dry-run", help="Report what would be published."),
) -> None:
    counts = publish(dry_run=dry_run)
    total = sum(counts.values())
    logger.success("{} table(s), {} rows total", len(counts), total)


if __name__ == "__main__":
    app()
