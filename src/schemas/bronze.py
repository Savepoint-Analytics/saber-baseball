"""Pandera schemas that gate ingested bronze tables before they advance to silver.

Every ingested table is validated against a schema here (``CLAUDE.md`` §6: "validate
data with Pandera"). Schemas are intentionally permissive on columns we don't yet
model (``strict=False``) but strict on the keys and types we depend on. Tighten as
the silver layer firms up.

Usage::

    from src.schemas.bronze import validate_schedule
    clean = validate_schedule(df)   # raises pandera.errors.SchemaError on violation
"""

from __future__ import annotations

import pandas as pd
import pandera.pandas as pa
from pandera.pandas import Column, DataFrameSchema

# --- MLB Stats API schedule (game-level) ------------------------------------
SCHEDULE_SCHEMA = DataFrameSchema(
    {
        "game_pk": Column(pa.Int, nullable=False, unique=True, coerce=True),
        "game_date": Column(pa.String, nullable=False),
        "game_type": Column(pa.String, nullable=True),
        "season": Column(pa.Int, nullable=True, coerce=True),
        "status": Column(pa.String, nullable=True),
        "home_team_id": Column(pa.Int, nullable=True, coerce=True),
        "away_team_id": Column(pa.Int, nullable=True, coerce=True),
        "home_score": Column(pa.Float, nullable=True, coerce=True),
        "away_score": Column(pa.Float, nullable=True, coerce=True),
    },
    strict=False,
    coerce=True,
    name="statsapi_schedule",
)

# --- Statcast pitch-level (subset of the ~90 columns we rely on) ------------
STATCAST_SCHEMA = DataFrameSchema(
    {
        "game_pk": Column(pa.Int, nullable=True, coerce=True),
        "game_date": Column(pa.String, nullable=True, coerce=True),
        "pitch_type": Column(pa.String, nullable=True),
        "release_speed": Column(pa.Float, pa.Check.in_range(0, 110), nullable=True, coerce=True),
        "events": Column(pa.String, nullable=True),
        "description": Column(pa.String, nullable=True),
        "batter": Column(pa.Int, nullable=True, coerce=True),
        "pitcher": Column(pa.Int, nullable=True, coerce=True),
    },
    strict=False,
    coerce=True,
    name="statcast_pitches",
)

# --- Lahman Teams (season-level) --------------------------------------------
# W/L/R/RA use pandas' *nullable* Int64, not numpy int64. SABR's Lahman release
# includes Negro Leagues seasons licensed from Seamheads where full statistics do not
# survive — the 1932 Negro Southern League Cole's American Giants have a games/wins
# record but no run totals. numpy int64 cannot represent that gap, so coercion fails
# outright and a plain float column would quietly turn every run total into a float.
LAHMAN_TEAMS_SCHEMA = DataFrameSchema(
    {
        "yearID": Column(pa.Int, nullable=False, coerce=True),
        "teamID": Column(pa.String, nullable=False),
        "W": Column("Int64", pa.Check.ge(0), nullable=True, coerce=True),
        "L": Column("Int64", pa.Check.ge(0), nullable=True, coerce=True),
        "R": Column("Int64", pa.Check.ge(0), nullable=True, coerce=True),
        "RA": Column("Int64", pa.Check.ge(0), nullable=True, coerce=True),
    },
    strict=False,
    coerce=True,
    name="lahman_teams",
)


def validate_schedule(df: pd.DataFrame) -> pd.DataFrame:
    """Validate an MLB Stats API schedule frame."""
    return SCHEDULE_SCHEMA.validate(df, lazy=True)


def validate_statcast(df: pd.DataFrame) -> pd.DataFrame:
    """Validate a Statcast pitch-level frame."""
    return STATCAST_SCHEMA.validate(df, lazy=True)


def validate_lahman_teams(df: pd.DataFrame) -> pd.DataFrame:
    """Validate the Lahman Teams table."""
    return LAHMAN_TEAMS_SCHEMA.validate(df, lazy=True)
