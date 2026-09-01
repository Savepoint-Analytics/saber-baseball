"""Prefect flows: compliant bronze ingestion → dbt transforms → Postgres publish.

Wraps the Python pullers as Prefect tasks so runs get retries, logging, scheduling,
and a UI, then carries the chain all the way through to the serving warehouse:

    ingest (bronze Parquet) → dbt build (DuckDB) → publish (Postgres baseball.*)

Prefect (not Dagster) is the Savepoint orchestrator across every vertical — see
``registry.md`` §6. Savepoint-Lab runs ONE Prefect server shared by all of them, so
flow and deployment names are a global namespace: everything baseball registers is
prefixed ``baseball-``, the same rule as the ``baseball`` Postgres schema and the
``baseball/`` MinIO prefix.

Run::

    python -m orchestration.flows                      # serve scheduled deployments
    python -c "from orchestration.flows import daily_pipeline; daily_pipeline()"
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import date, timedelta
from pathlib import Path

from prefect import flow, get_run_logger, task
from prefect.schedules import Cron

REPO_ROOT = Path(__file__).resolve().parent.parent
DBT_DIR = REPO_ROOT / "dbt"

# Per-request rate limiting and HTTP backoff already live in
# ``src.ingestion._throttle`` (tenacity), so task-level retries exist only to cover
# whole-step failures — a source down for a minute, a transient S3 write. Keep them
# few and slow: hammering an upstream would breach the politeness rules in
# ``CLAUDE.md`` §5 that the throttle layer exists to enforce.
INGEST_RETRIES = {"retries": 2, "retry_delay_seconds": 60}


@task(name="chadwick-register", **INGEST_RETRIES)
def chadwick_register() -> str:
    """Chadwick player-ID crosswalk (open license)."""
    from src.ingestion import chadwick

    key = chadwick.ingest()
    get_run_logger().info("chadwick -> %s", key)
    return key


@task(name="lahman-tables", **INGEST_RETRIES)
def lahman_tables() -> list[str]:
    """Lahman season-level tables (CC BY-SA 3.0 — attribute, never redistribute raw)."""
    from src.ingestion import lahman

    keys = lahman.ingest()
    get_run_logger().info("lahman -> %d tables", len(keys))
    return keys


@task(name="statsapi-schedule", **INGEST_RETRIES)
def statsapi_schedule(season: int | None = None) -> str:
    """MLB Stats API schedule for one season (© MLBAM — derived analysis only)."""
    from src.ingestion import statsapi

    year = season or date.today().year
    start, end = statsapi.season_window(year)
    key = statsapi.ingest(start, end)
    get_run_logger().info("statsapi %s -> %s", year, key)
    return key


@task(name="statcast-recent", **INGEST_RETRIES)
def statcast_recent(day: date | None = None) -> list[str]:
    """Pitch-level Statcast for a single day (© MLBAM/Savant — derived only)."""
    from src.ingestion import statcast

    target = day or (date.today() - timedelta(days=1))
    keys = statcast.ingest(target, target)
    get_run_logger().info("statcast %s -> %d partitions", target, len(keys))
    return keys


@task(name="dbt-build")
def dbt_build(target: str = "duckdb") -> None:
    """Run ``dbt build`` (models + tests) over the bronze Parquet in DuckDB.

    DuckDB owns every transform because the bronze models are ``read_parquet()``
    views and Postgres has no such function — see ``docs/methodology.md``. A failing
    dbt test fails the flow, so nothing reaches Postgres unvalidated.
    """
    # Guard before touching the run logger so the failure is a plain, testable
    # error rather than a missing-context error from Prefect.
    if shutil.which("dbt") is None:
        raise RuntimeError(
            "dbt not on PATH — install the 'warehouse' extra (uv sync --extra warehouse)."
        )

    logger = get_run_logger()
    proc = subprocess.run(
        ["dbt", "build", "--target", target],
        cwd=DBT_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    logger.info(proc.stdout[-4000:] if proc.stdout else "(no dbt stdout)")
    if proc.returncode != 0:
        logger.error(proc.stderr[-2000:] if proc.stderr else "(no dbt stderr)")
        raise RuntimeError(f"dbt build --target {target} failed (exit {proc.returncode})")
    logger.info("dbt build --target %s succeeded", target)


@task(name="publish-warehouse")
def publish_warehouse(dry_run: bool = False) -> dict[str, int]:
    """Copy the modeled silver/gold marts from DuckDB into Postgres.

    Only curated layers are published; raw pitch-level data never enters the
    warehouse, which is a licensing property as much as a performance one
    (``CLAUDE.md`` §4).
    """
    from src.warehouse.load_postgres import publish

    counts = publish(dry_run=dry_run)
    get_run_logger().info("published %d table(s), %d rows", len(counts), sum(counts.values()))
    return counts


@flow(name="baseball-daily-pipeline", log_prints=True)
def daily_pipeline(
    season: int | None = None,
    day: date | None = None,
    include_reference: bool = True,
    publish_to_postgres: bool = True,
) -> dict[str, int]:
    """Full daily chain: bronze ingestion → dbt build + tests → Postgres publish.

    ``include_reference`` covers Chadwick and Lahman. They are season-level archives
    rather than daily feeds, but the HTTP cache (``SABER_HTTP_CACHE_TTL_DAYS``) makes
    a daily call cheap, and pulling them on the same cadence keeps the ID crosswalk
    from drifting behind the schedule data that joins to it.
    """
    logger = get_run_logger()

    if include_reference:
        chadwick_register()
        lahman_tables()

    statsapi_schedule(season=season)
    statcast_recent(day=day)

    # dbt is sequenced after every ingest deliberately: it reads the bronze Parquet
    # those tasks just landed, so overlapping them would build against a half-written
    # partition set.
    dbt_build()

    if not publish_to_postgres:
        logger.info("skipping Postgres publish (publish_to_postgres=False)")
        return {}
    return publish_warehouse()


def serve() -> None:
    """Serve the scheduled deployment (the long-running process in Compose)."""
    daily_pipeline.serve(
        name="baseball-daily",
        # 09:00 ET daily, after the prior day's games settle. The timezone must ride
        # on the schedule object: Flow.serve's bare `cron=` kwarg has no timezone
        # companion, so it would silently schedule in UTC.
        schedules=[Cron("0 9 * * *", timezone="America/New_York")],
    )


if __name__ == "__main__":
    serve()
