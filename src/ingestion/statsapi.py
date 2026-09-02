"""Ingest schedules / game-level data from the official MLB Stats API.

Compliance: ``statsapi.mlb.com`` data is MLB Advanced Media copyright — derived
analysis only, no resale of the raw feed; attribute MLBAM; rate-limit + cache
(handled by ``cached_get`` on the ``statsapi`` limiter). We use the official API
directly (no scraping). The ``MLB-StatsAPI`` library (GPLv3) is an optional helper,
used only as a runtime dependency for producing analysis — never bundled into a
closed, redistributed product (see ``registry.md``).

Run::

    python -m src.ingestion.statsapi --season 2025
    python -m src.ingestion.statsapi --start 2025-04-01 --end 2025-04-30
    python -m src.ingestion.statsapi --start-season 2015   # backfill → current year
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import typer
from loguru import logger

from src.compliance.attributions import Source
from src.ingestion._storage import bronze_key, write_parquet
from src.ingestion._throttle import cached_get

app = typer.Typer(add_completion=False, help="Ingest MLB Stats API schedules.")

SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"

# Season bounds, wide enough for international openers and a November World Series.
_SEASON_START = "{year}-03-01"
_SEASON_END = "{year}-11-30"


def _fetch_schedule(start: str, end: str, sport_id: int = 1) -> pd.DataFrame:
    """Fetch the schedule for a date range and flatten games to one row each."""
    payload = cached_get(
        SCHEDULE_URL,
        source="statsapi",
        params={
            "sportId": sport_id,
            "startDate": start,
            "endDate": end,
            "gameTypes": "R,F,D,L,W",  # regular + postseason rounds
            # Hydrating weather and venue costs nothing extra — it rides on the same
            # one-request-per-date-range call. Fetching weather per game instead would
            # be ~27,700 requests, roughly eight hours at our StatsAPI rate cap, for
            # data the schedule endpoint will hand over for free.
            #
            # probablePitcher is captured for a different reason: the starter features
            # in gd_starter_form are keyed off who actually started, which is fine for
            # a backtest but unavailable pre-game. The announced probable is what a
            # live deployment must use, so it is landed now rather than discovered
            # missing later.
            "hydrate": "weather,venue,probablePitcher",
        },
    )
    rows: list[dict] = []
    for day in payload.get("dates", []):  # type: ignore[union-attr]
        for g in day.get("games", []):
            teams = g.get("teams", {})
            rows.append(
                {
                    "game_pk": g.get("gamePk"),
                    "game_date": day.get("date"),
                    "game_type": g.get("gameType"),
                    "season": g.get("season"),
                    "status": g.get("status", {}).get("detailedState"),
                    "home_team_id": teams.get("home", {}).get("team", {}).get("id"),
                    "home_team": teams.get("home", {}).get("team", {}).get("name"),
                    "home_score": teams.get("home", {}).get("score"),
                    "away_team_id": teams.get("away", {}).get("team", {}).get("id"),
                    "away_team": teams.get("away", {}).get("team", {}).get("name"),
                    "away_score": teams.get("away", {}).get("score"),
                    "venue": g.get("venue", {}).get("name"),
                    "venue_id": g.get("venue", {}).get("id"),
                    "day_night": g.get("dayNight"),
                    # Weather is absent for postponed games and for some older
                    # seasons; downstream models must tolerate nulls rather than
                    # assume coverage.
                    "weather_condition": (g.get("weather") or {}).get("condition"),
                    "weather_temp": (g.get("weather") or {}).get("temp"),
                    "weather_wind": (g.get("weather") or {}).get("wind"),
                    "home_probable_pitcher_id": (
                        (teams.get("home", {}).get("probablePitcher") or {}).get("id")
                    ),
                    "away_probable_pitcher_id": (
                        (teams.get("away", {}).get("probablePitcher") or {}).get("id")
                    ),
                }
            )
    return pd.DataFrame(rows)


def season_window(season: int) -> tuple[str, str]:
    """Date window covering a full season: international openers → World Series.

    March 1 is early enough for the Tokyo/Seoul openers (2019, 2024 and 2025 all
    started March 18-20) and November 30 is late enough for any World Series.
    """
    return _SEASON_START.format(year=season), _SEASON_END.format(year=season)


def ingest(start: str, end: str) -> str:
    """Pull the schedule for [start, end] and land it as Parquet in bronze."""
    logger.info("StatsAPI: schedule {} -> {}", start, end)
    df = _fetch_schedule(start, end)
    season = start[:4]
    key = bronze_key("statsapi", f"season={season}", f"schedule_{start}_{end}.parquet")
    return write_parquet(df, key, sources=[Source.MLB_STATS_API])


def backfill(start_season: int, end_season: int) -> list[str]:
    """Ingest one schedule partition per season across [start_season, end_season].

    This is what makes a multi-season history reproducible from a clean checkout
    (``CLAUDE.md`` golden rule 1) rather than a property of one machine's data dir.
    Seasons are pulled independently so a transient failure on one does not discard
    the rest; failures are collected and raised once the loop finishes.
    """
    if end_season < start_season:
        raise ValueError(f"end_season {end_season} precedes start_season {start_season}")

    written: list[str] = []
    failed: list[int] = []
    for season in range(start_season, end_season + 1):
        try:
            written.append(ingest(*season_window(season)))
        except Exception as exc:  # keep going; a bad season shouldn't sink the backfill
            logger.error("StatsAPI: season {} failed: {}", season, exc)
            failed.append(season)

    logger.info("StatsAPI backfill: {} season(s) written, {} failed", len(written), len(failed))
    if failed:
        raise RuntimeError(f"StatsAPI backfill failed for season(s): {failed}")
    return written


@app.command()
def main(
    season: int = typer.Option(None, "--season", help="Full-season shortcut."),
    start_season: int = typer.Option(
        None, "--start-season", help="First season of a multi-season backfill."
    ),
    end_season: int = typer.Option(
        None, "--end-season", help="Last season of a backfill (default: current year)."
    ),
    start: str = typer.Option(None, "--start", help="YYYY-MM-DD inclusive."),
    end: str = typer.Option(None, "--end", help="YYYY-MM-DD inclusive."),
) -> None:
    if start_season:
        keys = backfill(start_season, end_season or date.today().year)
        logger.success("StatsAPI backfill complete: {} season(s)", len(keys))
        return
    if season:
        start, end = season_window(season)
    elif not (start and end):
        raise typer.BadParameter("Provide --season, --start-season, OR both --start and --end.")
    key = ingest(start, end)
    logger.success("StatsAPI ingest complete -> {}", key)


if __name__ == "__main__":
    app()
