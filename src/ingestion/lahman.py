"""Ingest the Lahman Baseball Database (season-level history back to 1871).

Compliance: Lahman is CC BY-SA 3.0 — commercial use OK with attribution. We do NOT
redistribute the raw tables as a dataset; we land them in our private bronze zone for
internal modeling and ship only derived analysis. (See ``public-data-sources.md``.)

Implementation note: we download the Chadwick Bureau's ``baseballdatabank`` archive
ourselves rather than via ``pybaseball.lahman``. pybaseball 2.2.7 hardcodes the repo's
old ``master.zip`` URL, which now 404s (the repo's default branch was renamed to
``main``) and yields a ``BadZipFile`` — see pybaseball issue #391. Fetching the archive
directly keeps us in control of the URL, our own cache, and the core/contrib paths.

Run::

    python -m src.ingestion.lahman                 # core tables
    python -m src.ingestion.lahman --tables Teams Batting Pitching
"""

from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path

import pandas as pd
import requests
import typer
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from src.compliance.attributions import Source
from src.config import settings
from src.ingestion._storage import bronze_key, write_parquet

app = typer.Typer(add_completion=False, help="Ingest Lahman season-level tables.")

# Chadwick Bureau baseballdatabank = the maintained Lahman database (CC BY-SA 3.0).
_REPO = "chadwickbureau/baseballdatabank"
_API_REPO = f"https://api.github.com/repos/{_REPO}"
_ARCHIVE_TMPL = "https://github.com/{repo}/archive/refs/heads/{branch}.zip"


def _candidate_urls() -> list[str]:
    """Archive URLs to try, in order.

    1. Full override via ``SABER_LAHMAN_ARCHIVE_URL`` (zip URL or local file path).
    2. The repo's *current* default branch, resolved from the GitHub API — so this
       keeps working regardless of any future master/main rename.
    3. Static fallbacks (``main`` then ``master``) if the API is unreachable.
    """
    override = os.getenv("SABER_LAHMAN_ARCHIVE_URL")
    urls: list[str] = [override] if override else []
    try:
        meta = requests.get(
            _API_REPO, headers={"User-Agent": settings.user_agent}, timeout=30
        ).json()
        default_branch = meta.get("default_branch")
        if default_branch:
            urls.append(_ARCHIVE_TMPL.format(repo=_REPO, branch=default_branch))
    except Exception as exc:  # API blocked/offline — fall back to static guesses.
        logger.warning("Lahman: could not resolve default branch via API: {}", exc)
    for branch in ("main", "master"):
        url = _ARCHIVE_TMPL.format(repo=_REPO, branch=branch)
        if url not in urls:
            urls.append(url)
    return urls

DEFAULT_TABLES = ["Teams", "People", "Batting", "Pitching", "Fielding"]

# Logical table -> CSV basename. Files live under core/ or contrib/ in the archive;
# we search both so we don't hard-code which folder a table lives in.
_TABLE_FILES: dict[str, str] = {
    "Teams": "Teams.csv",
    "People": "People.csv",
    "Batting": "Batting.csv",
    "Pitching": "Pitching.csv",
    "Fielding": "Fielding.csv",
    "Salaries": "Salaries.csv",
    "AwardsPlayers": "AwardsPlayers.csv",
    "AllstarFull": "AllstarFull.csv",
}


@retry(wait=wait_exponential(multiplier=1, min=2, max=60), stop=stop_after_attempt(5), reraise=True)
def _download_archive() -> bytes:
    """Fetch the baseballdatabank zip, raising clearly if none is a real zip.

    Honors a local-file override: if ``SABER_LAHMAN_ARCHIVE_URL`` points at an existing
    file, read it directly (handy for fully-offline / air-gapped reproducible runs).
    """
    headers = {"User-Agent": settings.user_agent}
    last_err: Exception | None = None
    for url in _candidate_urls():
        if os.path.exists(url):  # local file override
            data = Path(url).read_bytes()
            if data[:2] == b"PK":
                logger.info("Lahman: using local archive {}", url)
                return data
            last_err = RuntimeError(f"local file {url} is not a zip")
            continue
        logger.info("Lahman: downloading {}", url)
        resp = requests.get(url, headers=headers, timeout=120)
        if resp.status_code == 200 and resp.content[:2] == b"PK":
            return resp.content
        last_err = RuntimeError(
            f"{url} returned status={resp.status_code}, "
            f"content-type={resp.headers.get('content-type')!r} (not a zip)"
        )
        logger.warning("Lahman: {} not usable ({})", url, last_err)
    raise RuntimeError(f"Could not download a valid baseballdatabank archive: {last_err}")


def _cached_archive() -> zipfile.ZipFile:
    """Return the archive as a ZipFile, caching the raw zip under the data cache dir."""
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = Path(settings.cache_dir) / "baseballdatabank.zip"
    if not cache_file.exists() or cache_file.stat().st_size == 0:
        cache_file.write_bytes(_download_archive())
        logger.debug("Lahman: cached archive -> {}", cache_file)
    return zipfile.ZipFile(io.BytesIO(cache_file.read_bytes()))


def _read_table(zf: zipfile.ZipFile, table: str) -> pd.DataFrame:
    """Read a logical table's CSV from the archive (searching core/ and contrib/)."""
    fname = _TABLE_FILES.get(table)
    if fname is None:
        raise ValueError(f"Unknown Lahman table: {table!r}. Known: {sorted(_TABLE_FILES)}")
    matches = [n for n in zf.namelist() if n.endswith(f"/core/{fname}") or n.endswith(f"/contrib/{fname}")]
    if not matches:
        # Fall back to any path ending in the filename (layout drift safety net).
        matches = [n for n in zf.namelist() if n.endswith(f"/{fname}")]
    if not matches:
        raise FileNotFoundError(f"{fname} not found in baseballdatabank archive for table {table!r}")
    with zf.open(matches[0]) as fh:
        return pd.read_csv(fh)


def ingest(tables: list[str] | None = None) -> list[str]:
    """Pull each Lahman table and land it as Parquet in bronze. Returns written keys."""
    tables = tables or DEFAULT_TABLES
    zf = _cached_archive()
    written: list[str] = []
    for table in tables:
        logger.info("Lahman: loading {}", table)
        df = _read_table(zf, table)
        key = bronze_key("lahman", f"table={table}", f"{table}.parquet")
        written.append(write_parquet(df, key, sources=[Source.LAHMAN]))
    return written


@app.command()
def main(
    tables: list[str] = typer.Option(None, "--tables", help="Subset of tables to pull."),
) -> None:
    keys = ingest(tables or None)
    logger.success("Lahman ingest complete: {} tables", len(keys))


if __name__ == "__main__":
    app()
