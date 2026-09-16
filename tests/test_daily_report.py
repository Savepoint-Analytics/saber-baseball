"""The daily prediction report.

The property that matters most is the one every backtest in this project quietly
assumed away: a game that has not been played has no Statcast, so starter form has to
come from the *announced probable* rather than from whoever turns out to start. If
that lookup silently produced nulls the report would still render, and every game
would quietly fall back to the team rating.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from src.models.backtest import STARTER_COLUMNS
from src.serving.daily_report import ATTRIBUTION_SOURCES, _shown, build_features, load_slate
from tests.conftest import requires_lake

needs_lake = requires_lake()


def test_report_attributes_its_sources():
    """Anything published on Statcast or the Stats API must carry the MLBAM notice."""
    from src.compliance.attributions import Source, attribution_block

    assert Source.STATCAST in ATTRIBUTION_SOURCES
    assert Source.MLB_STATS_API in ATTRIBUTION_SOURCES
    assert "MLB Advanced Media" in attribution_block(ATTRIBUTION_SOURCES)


def test_build_features_attaches_form_for_known_starters():
    slate = pd.DataFrame(
        {
            "game_pk": [1, 2],
            "game_date": [pd.Timestamp("2026-09-04")] * 2,
            "season": [2026, 2026],
            "home_team_id": [10, 11],
            "away_team_id": [11, 10],
            "home_probable_pitcher_id": [100, 999],  # second is unknown
            "away_probable_pitcher_id": [101, 102],
        }
    )
    form = pd.DataFrame(
        {"pitcher": [100, 101, 102], **{c: [0.3, 0.4, 0.5] for c in STARTER_COLUMNS}}
    )
    out = build_features(slate, form)
    assert out["home_sp_form_xwoba_allowed"].iloc[0] == pytest.approx(0.3)
    assert out["away_sp_form_xwoba_allowed"].iloc[0] == pytest.approx(0.4)
    # An unknown probable must be null, not zero -- zero is a real (excellent) value.
    assert np.isnan(out["home_sp_form_xwoba_allowed"].iloc[1])


def test_unplayed_games_get_null_outcomes_not_fabricated_ones():
    """The model's null handling depends on these being absent, not invented."""
    slate = pd.DataFrame(
        {
            "game_pk": [1],
            "game_date": [pd.Timestamp("2026-09-04")],
            "season": [2026],
            "home_team_id": [10],
            "away_team_id": [11],
            "home_probable_pitcher_id": [100],
            "away_probable_pitcher_id": [101],
        }
    )
    out = build_features(slate, pd.DataFrame({"pitcher": [100, 101]}))
    for col in ("home_score", "away_score", "home_win"):
        assert out[col].isna().all()


@needs_lake
def test_todays_slate_loads_with_probables():
    slate = load_slate(date.today())
    if slate.empty:
        pytest.skip("no games scheduled today")
    assert {"home_probable_pitcher_id", "away_probable_pitcher_id"} <= set(slate.columns)
    assert slate.game_pk.is_unique


@needs_lake
def test_predictions_are_probabilities_and_sensible_scores():
    from src.serving.daily_report import predict_slate

    games = predict_slate(date.today())
    if games.empty:
        pytest.skip("no games scheduled today")
    assert games.home_win_pct.between(5, 95).all(), "a win probability left the plausible range"
    assert games.proj_home.between(1.5, 9.0).all()
    assert games.proj_away.between(1.5, 9.0).all()


def test_missing_weather_renders_instead_of_crashing():
    """A game with no posted forecast is normal — StatsAPI populates weather closer
    to first pitch. `pd.NA or "?"` raises, so the report used to die on the whole
    slate because one game had no temperature."""
    assert _shown(pd.NA) == "?"
    assert _shown(None) == "?"
    assert _shown(np.nan) == "?"
    assert _shown(72) == "72"
    assert _shown("Partly Cloudy") == "Partly Cloudy"
