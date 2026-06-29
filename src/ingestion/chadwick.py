"""Ingest the Chadwick Bureau player-ID register (crosswalk across ID systems).

Compliance: Chadwick is open; commercial use OK. It is the essential glue for
joining Retrosheet ↔ MLBAM ↔ Baseball-Reference ↔ FanGraphs IDs.

Run::

    python -m src.ingestion.chadwick
"""

from __future__ import annotations

import typer
from loguru import logger

from src.compliance.attributions import Source
from src.ingestion._storage import bronze_key, write_parquet
from src.ingestion._throttle import enable_pybaseball_cache, with_backoff

app = typer.Typer(add_completion=False, help="Ingest the Chadwick ID crosswalk.")


@with_backoff
def _load_register():
    # pybaseball.chadwick_register() downloads + caches the full people register.
    from pybaseball import chadwick_register

    return chadwick_register()


def ingest() -> str:
    """Pull the Chadwick register and land it as Parquet in bronze."""
    enable_pybaseball_cache()
    logger.info("Chadwick: loading player register")
    df = _load_register()
    key = bronze_key("chadwick", "register.parquet")
    return write_parquet(df, key, sources=[Source.CHADWICK])


@app.command()
def main() -> None:
    key = ingest()
    logger.success("Chadwick ingest complete -> {}", key)


if __name__ == "__main__":
    app()
