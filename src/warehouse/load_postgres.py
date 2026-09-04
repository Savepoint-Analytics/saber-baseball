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

Everything is published into the vertical's own Postgres schema
(``POSTGRES_SCHEMA``, default ``baseball``). Savepoint-Lab hosts one ``sports``
database for every vertical, so a schema per vertical is what keeps baseball's
marts from colliding with tennis's.

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
#
# Keys are the DuckDB layer (dbt materializes them as `main_silver` / `main_gold`);
# values are the tables to copy. In Postgres every table lands in ONE schema — the
# vertical's own (`settings.postgres_schema`, e.g. `baseball`) — because
# Savepoint-Lab runs a single `sports` database shared with the other verticals.
# The layer survives in the table name's `sv_` / `gd_` prefix, so nothing is lost by
# flattening, and `baseball.sv_games` cannot collide with `tennis.silver_matches`.
# The dividing line is "would a dashboard, a report, or a BI user query this?" --
# not "is it derived?". Three kinds of table are deliberately absent:
#
#   * **Plate-appearance detail.** sv_plate_appearances and sv_lineup_slots are 2.1M
#     rows each of pitch-derived state. They are the simulation engine's raw material,
#     they are close enough to the Statcast feed that they belong in the lake on
#     licensing grounds (CLAUDE.md section 4), and no dashboard joins them.
#   * **Engine internals.** gd_base_transitions, gd_run_env_direction and
#     gd_pitcher_form_direction are calibration inputs the simulator reads. Publishing
#     them would invite someone to treat a probability lookup as an analytical fact.
#   * **Intermediate components.** sv_statcast_bullpen_game exists to build
#     gd_bullpen_form; the rolled-up version is the one worth serving.
#
# Everything else that a person might reasonably ask a question of is published.
PUBLISHED: dict[str, tuple[str, ...]] = {
    "silver": (
        "sv_games",
        "sv_statcast_team_game",
        "sv_statcast_pitcher_game",
    ),
    "gold": (
        "gd_team_season_record",
        "gd_run_expectancy",
        "gd_park_factor",
        "gd_weather_factor",
        "gd_team_form",
        "gd_starter_form",
        "gd_bullpen_form",
        "gd_pitcher_removal",
    ),
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

    # Fail with the missing name rather than a raw catalog error from three frames
    # down. A table listed in PUBLISHED but absent from the lake means dbt has not
    # been run, or a model was renamed and this list was not updated -- both worth
    # saying out loud.
    present = {
        f"{r[0]}.{r[1]}"
        for r in con.sql("select schema_name, table_name from duckdb_tables()").fetchall()
    }
    wanted = {f"main_{layer}.{t}" for layer, tables in PUBLISHED.items() for t in tables}
    missing = sorted(wanted - present)
    if missing:
        raise ValueError(
            f"PUBLISHED lists {len(missing)} table(s) absent from the lake: {missing}. "
            "Run `make dbt-duckdb`, or update PUBLISHED if a model was renamed."
        )

    con.execute("INSTALL postgres; LOAD postgres;")
    con.execute(f"ATTACH '{settings.postgres_dsn}' AS pg (TYPE POSTGRES)")
    logger.info("attached postgres at {}:{}", settings.postgres_host, settings.postgres_port)

    warehouse_schema = settings.postgres_schema
    if not dry_run:
        con.execute(f"CREATE SCHEMA IF NOT EXISTS pg.{warehouse_schema}")

    counts: dict[str, int] = {}
    plan: list[tuple[str, str, int]] = []
    for layer, tables in PUBLISHED.items():
        for table in tables:
            source = f"main_{layer}.{table}"
            target = f"pg.{warehouse_schema}.{table}"
            row = con.sql(f"select count(*) from {source}").fetchone()
            rows = int(row[0]) if row else 0
            counts[f"{warehouse_schema}.{table}"] = rows
            plan.append((source, target, rows))

    if dry_run:
        for source, target, rows in plan:
            logger.info("[dry-run] would publish {} rows: {} -> {}", rows, source, target)
        con.close()
        return counts

    # All marts land or none do. Publishing table-by-table outside a transaction
    # means a failure halfway leaves Postgres serving a new sv_games beside a stale
    # gd_team_season_record — two tables that disagree about the same season, which
    # is worse for a BI consumer than simply being a day behind. Postgres DDL is
    # transactional, so the replaces roll back together.
    con.execute("BEGIN TRANSACTION")
    try:
        for source, target, rows in plan:
            con.execute(f"CREATE OR REPLACE TABLE {target} AS SELECT * FROM {source}")
            logger.success("published {} rows: {} -> {}", rows, source, target)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        logger.error("publish failed; rolled back — Postgres left as it was")
        raise
    finally:
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
