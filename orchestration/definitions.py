"""Dagster definitions: ingestion assets + a daily schedule.

Wraps the compliant Python pullers as Dagster software-defined assets so runs get
lineage, retries, and scheduling. dbt is wired as a downstream step once the project
is built locally (left as a documented TODO to avoid importing dbt at module load).

Launch::

    dagster dev -m orchestration.definitions
"""

from __future__ import annotations

from datetime import date

from dagster import (
    AssetExecutionContext,
    Definitions,
    ScheduleDefinition,
    asset,
    define_asset_job,
)


@asset(group_name="bronze", description="Chadwick player-ID crosswalk (open).")
def chadwick_register(context: AssetExecutionContext) -> str:
    from src.ingestion import chadwick

    key = chadwick.ingest()
    context.log.info(f"chadwick -> {key}")
    return key


@asset(group_name="bronze", description="Lahman season-level tables (CC BY-SA 3.0).")
def lahman_tables(context: AssetExecutionContext) -> list[str]:
    from src.ingestion import lahman

    keys = lahman.ingest()
    context.log.info(f"lahman -> {len(keys)} tables")
    return keys


@asset(group_name="bronze", description="MLB Stats API schedule (© MLBAM, derived only).")
def statsapi_schedule(context: AssetExecutionContext) -> str:
    from src.ingestion import statsapi

    year = date.today().year
    key = statsapi.ingest(f"{year}-03-01", f"{year}-11-30")
    context.log.info(f"statsapi -> {key}")
    return key


@asset(
    group_name="bronze",
    deps=[statsapi_schedule],
    description="Pitch-level Statcast for yesterday (© MLBAM/Savant, derived only).",
)
def statcast_recent(context: AssetExecutionContext) -> list[str]:
    from datetime import timedelta

    from src.ingestion import statcast

    yday = date.today() - timedelta(days=1)
    keys = statcast.ingest(yday, yday)
    context.log.info(f"statcast -> {len(keys)} partitions")
    return keys


# TODO(Phase 1+): add a dbt asset via dagster-dbt's @dbt_assets once `dbt build`
# runs cleanly locally, making silver/gold downstream of these bronze assets.

daily_ingest_job = define_asset_job(name="daily_ingest", selection="*")

daily_schedule = ScheduleDefinition(
    name="daily_ingest_schedule",
    job=daily_ingest_job,
    cron_schedule="0 9 * * *",  # 09:00 daily; after prior day's games settle
    execution_timezone="America/New_York",
)

defs = Definitions(
    assets=[chadwick_register, lahman_tables, statsapi_schedule, statcast_recent],
    jobs=[daily_ingest_job],
    schedules=[daily_schedule],
)
