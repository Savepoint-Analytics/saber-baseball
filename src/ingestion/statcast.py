"""Ingest pitch-level Statcast data (Baseball Savant) into bronze.

Compliance (CRITICAL — see ``public-data-sources.md`` and ``CLAUDE.md`` §5):
Statcast is under MLB Advanced Media copyright, licensed for personal/non-commercial
use. We land it privately for **derived analysis only** and NEVER resell or
redistribute the raw feed. We rate-limit (``savant`` limiter), cache (pybaseball +
on-disk), and stamp the MLBAM attribution. Output is date-partitioned Parquet.

Run::

    python -m src.ingestion.statcast --start 2025-04-01 --end 2025-04-07
    python -m src.ingestion.statcast --season 2025   # full regular-season window
    python -m src.ingestion.statcast --start-season 2015 --end-season 2026  # backfill
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import typer
from loguru import logger

from src.compliance.attributions import Source
from src.config import settings
from src.ingestion._storage import bronze_duckdb, bronze_key, write_parquet
from src.ingestion._throttle import (
    enable_pybaseball_cache,
    get_limiter,
    with_backoff,
)

app = typer.Typer(add_completion=False, help="Ingest pitch-level Statcast data.")

# Rough regular-season bounds; refine per-season from the schedule when needed.
_SEASON_START = "{year}-03-20"
_SEASON_END = "{year}-10-05"


@with_backoff
def _pull_day(day: date) -> pd.DataFrame:
    from pybaseball import statcast

    get_limiter("savant").acquire()
    iso = day.isoformat()
    # Single-day pulls keep each request small and cache-friendly.
    return statcast(start_dt=iso, end_dt=iso)


def _daterange(start: date, end: date):
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def ingest(start: date, end: date) -> list[str]:
    """Pull Statcast day-by-day across [start, end]; land one Parquet per day."""
    enable_pybaseball_cache()
    written: list[str] = []
    for day in _daterange(start, end):
        df = _pull_day(day)
        if df is None or df.empty:
            logger.debug("Statcast: no rows for {} (off day?)", day)
            continue
        key = bronze_key(
            "statcast",
            f"season={day.year}",
            f"date={day.isoformat()}.parquet",
        )
        written.append(write_parquet(df, key, sources=[Source.STATCAST]))
    logger.info("Statcast: wrote {} day-partitions", len(written))
    return written


def _partition_key(day: date) -> str:
    """Bronze key for one day of Statcast."""
    return bronze_key("statcast", f"season={day.year}", f"date={day.isoformat()}.parquet")


def game_dates(start_season: int, end_season: int) -> list[date]:
    """Dates that actually had completed games, read from the bronze StatsAPI schedule.

    Driving the backfill off the real schedule rather than a fixed March-October
    window matters twice over: it picks up the postseason and the international
    openers a fixed window clips, and it skips every off-day, which is ~25% of the
    calendar. Fewer pointless requests to Savant is a compliance property, not just
    a speed one (``CLAUDE.md`` §5).

    Reads bronze Parquet directly rather than the dbt silver table so a backfill
    does not require the warehouse to have been built first.
    """
    glob = f"{settings.bronze_root}/statsapi/**/*.parquet"
    rows = (
        bronze_duckdb()
        .sql(
            f"""
        select distinct cast(game_date as date) as d
        from read_parquet('{glob}', union_by_name=true)
        where status in ('Final', 'Completed Early')
          and cast(season as integer) between {start_season} and {end_season}
        order by d
        """
        )
        .fetchall()
    )
    return [r[0] for r in rows]


def backfill(start_season: int, end_season: int, *, skip_existing: bool = True) -> list[str]:
    """Backfill Statcast across whole seasons, one Parquet per game date.

    Resumable by design: ``skip_existing`` means an interrupted multi-hour run picks
    up where it stopped instead of re-pulling days already on disk. A failing day is
    logged and skipped rather than sinking the run; the failures are raised together
    at the end so nothing fails silently.
    """
    if end_season < start_season:
        raise ValueError(f"end_season {end_season} precedes start_season {start_season}")

    enable_pybaseball_cache()
    days = game_dates(start_season, end_season)
    logger.info(
        "Statcast backfill: {} game dates across {}-{}", len(days), start_season, end_season
    )

    written: list[str] = []
    skipped = 0
    failed: list[str] = []
    for i, day in enumerate(days, 1):
        key = _partition_key(day)
        if skip_existing and Path(key).exists():
            skipped += 1
            continue
        try:
            df = _pull_day(day)
        except Exception as exc:
            logger.error("Statcast: {} failed: {}", day, exc)
            failed.append(day.isoformat())
            continue
        if df is None or df.empty:
            logger.debug("Statcast: no rows for {}", day)
            continue
        written.append(write_parquet(df, key, sources=[Source.STATCAST]))
        if i % 25 == 0:
            logger.info("Statcast backfill: {}/{} days ({} written)", i, len(days), len(written))

    logger.info(
        "Statcast backfill done: {} written, {} already present, {} failed",
        len(written),
        skipped,
        len(failed),
    )
    if failed:
        raise RuntimeError(f"Statcast backfill failed for {len(failed)} date(s): {failed[:10]}")
    return written


def _parse(d: str) -> date:
    return datetime.strptime(d, "%Y-%m-%d").date()


@app.command()
def main(
    start: str = typer.Option(None, "--start", help="YYYY-MM-DD inclusive."),
    end: str = typer.Option(None, "--end", help="YYYY-MM-DD inclusive."),
    season: int = typer.Option(None, "--season", help="Full season window shortcut."),
    start_season: int = typer.Option(None, "--start-season", help="Backfill first season."),
    end_season: int = typer.Option(None, "--end-season", help="Backfill last season."),
) -> None:
    if start_season:
        keys = backfill(start_season, end_season or date.today().year)
        logger.success("Statcast backfill complete: {} partitions", len(keys))
        return
    if season:
        start_d = _parse(_SEASON_START.format(year=season))
        end_d = _parse(_SEASON_END.format(year=season))
    elif start and end:
        start_d, end_d = _parse(start), _parse(end)
    else:
        raise typer.BadParameter("Provide --season OR both --start and --end.")
    keys = ingest(start_d, end_d)
    logger.success("Statcast ingest complete: {} partitions", len(keys))


if __name__ == "__main__":
    app()
