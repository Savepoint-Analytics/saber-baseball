"""Ingest pitch-level Statcast data (Baseball Savant) into bronze.

Compliance (CRITICAL — see ``public-data-sources.md`` and ``CLAUDE.md`` §5):
Statcast is under MLB Advanced Media copyright, licensed for personal/non-commercial
use. We land it privately for **derived analysis only** and NEVER resell or
redistribute the raw feed. We rate-limit (``savant`` limiter), cache (pybaseball +
on-disk), and stamp the MLBAM attribution. Output is date-partitioned Parquet.

Run::

    python -m src.ingestion.statcast --start 2025-04-01 --end 2025-04-07
    python -m src.ingestion.statcast --season 2025   # full regular-season window
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
import typer
from loguru import logger

from src.compliance.attributions import Source
from src.ingestion._storage import bronze_key, write_parquet
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


def _parse(d: str) -> date:
    return datetime.strptime(d, "%Y-%m-%d").date()


@app.command()
def main(
    start: str = typer.Option(None, "--start", help="YYYY-MM-DD inclusive."),
    end: str = typer.Option(None, "--end", help="YYYY-MM-DD inclusive."),
    season: int = typer.Option(None, "--season", help="Full season window shortcut."),
) -> None:
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
