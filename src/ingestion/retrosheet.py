"""Ingest Retrosheet play-by-play event data into bronze.

Compliance: Retrosheet permits ANY use (including commercial) **only with** its exact
copyright notice displayed. That notice is stamped via the NOTICE.txt written beside
the data and must appear on any deliverable built from it (see
``src/compliance/attributions.py`` ``Source.RETROSHEET``).

Retrosheet event data is the foundation for run expectancy (RE24) and WPA — Phase 2.

Run::

    python -m src.ingestion.retrosheet --season 2024
"""

from __future__ import annotations

import pandas as pd
import typer
from loguru import logger

from src.compliance.attributions import Source
from src.ingestion._storage import bronze_key, write_parquet
from src.ingestion._throttle import enable_pybaseball_cache, with_backoff

app = typer.Typer(add_completion=False, help="Ingest Retrosheet event data.")


@with_backoff
def _pull_events(season: int) -> pd.DataFrame:
    # pybaseball.retrosheet.events downloads + parses the season event files.
    from pybaseball.retrosheet import events

    return events(season)


def ingest(season: int) -> str:
    """Pull one season of Retrosheet events; land as Parquet in bronze."""
    enable_pybaseball_cache()
    logger.info("Retrosheet: loading {} events", season)
    df = _pull_events(season)
    key = bronze_key("retrosheet", f"season={season}", f"events_{season}.parquet")
    return write_parquet(df, key, sources=[Source.RETROSHEET])


@app.command()
def main(
    season: int = typer.Option(..., "--season", help="Season year, e.g. 2024."),
) -> None:
    key = ingest(season)
    logger.success("Retrosheet ingest complete -> {}", key)


if __name__ == "__main__":
    app()
