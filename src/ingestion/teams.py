"""Ingest the MLB team dimension — league, division, venue — into bronze.

Compliance: ``statsapi.mlb.com`` is MLB Advanced Media copyright, derived analysis
only, rate-limited and cached like every other MLBAM pull (``CLAUDE.md`` §5).

Exists because a playoff bracket is not derivable from game results. Seeding is by
division winner and wild card, so a season projection needs to know which division
each club is in, and the schedule feed does not carry it. One request per season
returns all 30 clubs.

Run::

    python -m src.ingestion.teams --season 2026
    python -m src.ingestion.teams --start-season 2015
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import typer
from loguru import logger

from src.compliance.attributions import Source
from src.ingestion._storage import bronze_key, write_parquet
from src.ingestion._throttle import cached_get

app = typer.Typer(add_completion=False, help="Ingest the MLB team dimension.")

TEAMS_URL = "https://statsapi.mlb.com/api/v1/teams"


def _fetch_teams(season: int, sport_id: int = 1) -> pd.DataFrame:
    payload = cached_get(
        TEAMS_URL, source="statsapi", params={"sportId": sport_id, "season": season}
    )
    rows = []
    for t in payload.get("teams", []):  # type: ignore[union-attr]
        rows.append(
            {
                "season": season,
                "team_id": t.get("id"),
                "team_name": t.get("name"),
                "abbreviation": t.get("abbreviation"),
                "league_id": (t.get("league") or {}).get("id"),
                "league_name": (t.get("league") or {}).get("name"),
                "division_id": (t.get("division") or {}).get("id"),
                "division_name": (t.get("division") or {}).get("name"),
                "venue_id": (t.get("venue") or {}).get("id"),
                "venue_name": (t.get("venue") or {}).get("name"),
                "active": t.get("active"),
            }
        )
    return pd.DataFrame(rows)


def ingest(season: int) -> str:
    """Pull one season's team dimension; land as Parquet in bronze."""
    df = _fetch_teams(season)
    if df.empty:
        raise RuntimeError(f"StatsAPI returned no teams for {season}")
    key = bronze_key("teams", f"season={season}", f"teams_{season}.parquet")
    logger.info("Teams {}: {} clubs", season, len(df))
    return write_parquet(df, key, sources=[Source.MLB_STATS_API])


def backfill(start_season: int, end_season: int) -> list[str]:
    """Ingest the team dimension across a season range.

    Divisions are not static — the Astros moved leagues in 2013 and clubs relocate —
    so the dimension is keyed by season rather than pulled once and reused.
    """
    if end_season < start_season:
        raise ValueError(f"end_season {end_season} precedes start_season {start_season}")
    written, failed = [], []
    for season in range(start_season, end_season + 1):
        try:
            written.append(ingest(season))
        except Exception as exc:
            logger.error("Teams: season {} failed: {}", season, exc)
            failed.append(season)
    if failed:
        raise RuntimeError(f"Teams backfill failed for season(s): {failed}")
    return written


@app.command()
def main(
    season: int = typer.Option(None, "--season"),
    start_season: int = typer.Option(None, "--start-season"),
    end_season: int = typer.Option(None, "--end-season"),
) -> None:
    if start_season:
        keys = backfill(start_season, end_season or date.today().year)
        logger.success("Teams backfill complete: {} season(s)", len(keys))
        return
    key = ingest(season or date.today().year)
    logger.success("Teams ingest complete -> {}", key)


if __name__ == "__main__":
    app()
