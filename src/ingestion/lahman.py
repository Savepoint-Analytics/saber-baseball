"""Ingest the Lahman Baseball Database (season-level history back to 1871).

Compliance: Lahman is CC BY-SA 3.0 — commercial use OK with attribution. We do NOT
redistribute the raw tables as a dataset; we land them in our private bronze zone for
internal modeling and ship only derived analysis. (See ``public-data-sources.md``.)

**Source note (2026-08-31).** The Chadwick Bureau's ``baseballdatabank`` GitHub repo,
which this module used to download, now returns 404 — the repo is gone, so every
candidate archive URL (``main``, ``master``, and the GitHub-API-resolved default
branch) 404s and the ingest hard-fails. The database is now published by **SABR** at
<https://sabr.org/lahman-database/>, which links Box-hosted downloads; version 2025
(released 2026-01-02) covers 1871-2025. The only surviving GitHub mirror found
(``xorq-labs/baseballdatabank``) stops at 2021, which is too stale to project a
current season from, so we read SABR's copy.

The Box file ids change with each annual release, so nothing here hard-codes one: we
read the SABR page, follow its "Comma-delimited version" link, and index the shared
folder by filename at run time.

Run::

    python -m src.ingestion.lahman                 # core tables
    python -m src.ingestion.lahman --tables Teams Batting Pitching
"""

from __future__ import annotations

import io
import os
import re

import pandas as pd
import typer
from loguru import logger

from src.compliance.attributions import Source
from src.ingestion._storage import bronze_key, write_parquet
from src.ingestion._throttle import cached_get_bytes
from src.schemas.bronze import validate_lahman_teams

app = typer.Typer(add_completion=False, help="Ingest Lahman season-level tables.")

_SABR_PAGE = "https://sabr.org/lahman-database/"
_BOX_DOWNLOAD = "https://sabr.app.box.com/index.php"

# Tables needed for roster-aware projection work: team records, player identity,
# and season-level batting/pitching/fielding lines.
DEFAULT_TABLES = ["Teams", "People", "Batting", "Pitching", "Fielding", "Salaries"]

# Floors that catch a truncated or placeholder download. This module previously left
# 1-2 row stub files in bronze that read as "present" to every downstream check, so a
# broken pull looked exactly like a healthy one. Better to fail the ingest than to
# publish a mart built on two rows.
_MIN_ROWS: dict[str, int] = {
    "Teams": 2500,
    "People": 15000,
    "Batting": 90000,
    "Pitching": 40000,
    "Fielding": 100000,
    "Salaries": 20000,
}
_DEFAULT_MIN_ROWS = 100


def _shared_name() -> str:
    """Resolve the Box shared-link id for the comma-delimited Lahman release.

    ``SABER_LAHMAN_SHARED_NAME`` pins a specific release; otherwise we scrape the one
    link off the SABR page. Scraping a single page once per cache TTL is well inside
    polite use, and it means next January's release is picked up without a code change.
    """
    override = os.getenv("SABER_LAHMAN_SHARED_NAME")
    if override:
        return override

    html = cached_get_bytes(_SABR_PAGE, source="sabr").decode("utf-8", errors="replace")
    # The page lists SQL / Access / comma-delimited variants; we want the CSV one.
    match = re.search(
        r'<a href="https://sabr\.box\.com/s/([A-Za-z0-9]+)"[^>]*>\s*Comma-delimited version',
        html,
    )
    if not match:
        raise RuntimeError(
            f"Could not find the comma-delimited Lahman link on {_SABR_PAGE}. "
            "Set SABER_LAHMAN_SHARED_NAME to the Box shared id to override."
        )
    return match.group(1)


def _folder_index(shared_name: str) -> dict[str, str]:
    """Map ``filename -> Box file id`` for the shared release folder."""
    url = f"https://sabr.app.box.com/s/{shared_name}"
    html = cached_get_bytes(url, source="sabr").decode("utf-8", errors="replace")

    index: dict[str, str] = {}
    for m in re.finditer(r'"typedID":"(f_\d+)"', html):
        window = html[m.end() : m.end() + 600]
        name = re.search(r'"name":"([^"]+)"', window)
        if name and name.group(1).endswith(".csv"):
            index.setdefault(name.group(1), m.group(1))
    if not index:
        raise RuntimeError(f"No CSV files found in Lahman release folder {url}")
    logger.debug("Lahman: release folder indexes {} csv files", len(index))
    return index


def _download_csv(shared_name: str, file_id: str) -> bytes:
    return cached_get_bytes(
        _BOX_DOWNLOAD,
        source="sabr",
        params={
            "rm": "box_download_shared_file",
            "shared_name": shared_name,
            "file_id": file_id,
        },
    )


def _read_table(table: str, shared_name: str, index: dict[str, str]) -> pd.DataFrame:
    """Download and parse one logical table, enforcing its row floor."""
    fname = f"{table}.csv"
    if fname not in index:
        raise FileNotFoundError(f"{fname} not in the Lahman release. Available: {sorted(index)}")

    raw = _download_csv(shared_name, index[fname])
    if not raw:
        raise RuntimeError(f"Lahman {table}: empty download for {fname}")
    # SABR re-published several CSVs with UTF-8 byte-order marks in Feb 2026;
    # utf-8-sig strips one if present and is a no-op if not, so the first column
    # name does not silently become "﻿yearID" and break every downstream join.
    df = pd.read_csv(io.BytesIO(raw), encoding="utf-8-sig")

    floor = _MIN_ROWS.get(table, _DEFAULT_MIN_ROWS)
    if len(df) < floor:
        raise RuntimeError(
            f"Lahman {table}: got {len(df)} rows, expected >= {floor}. "
            "Refusing to land a truncated table (see module docstring)."
        )
    return df


def ingest(tables: list[str] | None = None) -> list[str]:
    """Pull each Lahman table and land it as Parquet in bronze. Returns written keys."""
    tables = tables or DEFAULT_TABLES
    shared_name = _shared_name()
    index = _folder_index(shared_name)
    logger.info("Lahman: release {} ({} csv files)", shared_name, len(index))

    written: list[str] = []
    for table in tables:
        logger.info("Lahman: loading {}", table)
        df = _read_table(table, shared_name, index)
        # Teams gates through Pandera — it is the table the warehouse actually joins
        # to, so a schema drift there is the one most worth catching at the door.
        if table == "Teams":
            validate_lahman_teams(df)
        key = bronze_key("lahman", f"table={table}", f"{table}.parquet")
        written.append(write_parquet(df, key, sources=[Source.LAHMAN]))
        logger.success("Lahman {}: {} rows", table, len(df))
    return written


@app.command()
def main(
    tables: list[str] = typer.Option(None, "--tables", help="Subset of tables to pull."),
) -> None:
    keys = ingest(tables or None)
    logger.success("Lahman ingest complete: {} tables", len(keys))


if __name__ == "__main__":
    app()
