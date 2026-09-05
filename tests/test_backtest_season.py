"""Playoff-odds calibration backtest.

The pure parts — reading outcomes off a season's games and scoring stated
percentages — are tested on synthetic fixtures. The lake-backed part is pinned on
one invariant the historical cut must satisfy: a rewound season has games left.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from src.config import settings
from src.models.backtest_season import (
    OUTCOMES,
    calibration_summary,
    outcomes_from_games,
    reliability,
)
from src.simulation.season import load_season_state, load_training_games

LAKE = (settings.data_dir / "saber.duckdb").resolve()
needs_lake = pytest.mark.skipif(not LAKE.exists(), reason="DuckDB lake not built")


def _season(results: list[tuple[int, int, int]], post: list[tuple[str, int, int, int]]):
    """Build a games frame: regular (home, away, home_win) + postseason (type, ...)."""
    reg = pd.DataFrame(results, columns=["home_team_id", "away_team_id", "home_win"]).assign(
        season_phase="regular", game_type="R"
    )
    ps = pd.DataFrame(post, columns=["game_type", "home_team_id", "away_team_id", "home_win"])
    ps["season_phase"] = "postseason"
    return pd.concat([reg, ps], ignore_index=True)


@pytest.fixture
def teams():
    # Two leagues, one division each; four clubs.
    return pd.DataFrame(
        {
            "team_id": [1, 2, 3, 4],
            "league_id": [103, 103, 104, 104],
            "division_id": [200, 200, 203, 203],
        }
    )


def test_outcomes_read_off_the_postseason_record(teams):
    games = _season(
        # 1 beats 2 twice, 3 beats 4 twice; 1 and 3 are division winners.
        [(1, 2, 1), (2, 1, 0), (3, 4, 1), (4, 3, 0)],
        # Wild card: 2 beats 4. Division series and pennants: 1 and 3. WS: 3 wins 4-0.
        [("F", 2, 4, 1), ("D", 1, 2, 1), ("D", 3, 4, 1), ("L", 1, 2, 1), ("L", 3, 4, 1)]
        + [("W", 3, 1, 1), ("W", 3, 1, 1), ("W", 1, 3, 0), ("W", 1, 3, 0)],
    )
    out = outcomes_from_games(games, teams).set_index("team_id")
    assert out.made_playoffs.to_dict() == {1: True, 2: True, 3: True, 4: True}
    assert out.won_division.to_dict() == {1: True, 2: False, 3: True, 4: False}
    assert out.won_pennant.to_dict() == {1: True, 2: False, 3: True, 4: False}
    assert out.won_world_series.to_dict() == {1: False, 2: False, 3: True, 4: False}
    assert out.actual_wins.to_dict() == {1: 2, 2: 0, 3: 2, 4: 0}


def test_division_tie_breaks_head_to_head(teams):
    """Equal records: the club with the better head-to-head record takes the title.

    Three real titles in the 12-team era were decided this way, so an alphabetical
    or first-seen fallback would score the projection against the wrong seed.
    """
    games = _season(
        # 1 and 2 finish 2-2 each; 2 won the season series 2-1 (the third game was
        # against another club).
        [(1, 2, 0), (2, 1, 1), (1, 2, 1), (1, 3, 1), (2, 4, 0), (3, 4, 1), (4, 3, 0)],
        [("W", 3, 2, 1), ("W", 3, 2, 1), ("W", 2, 3, 0), ("W", 2, 3, 0)],
    )
    out = outcomes_from_games(games, teams).set_index("team_id")
    assert out.loc[1, "actual_wins"] == out.loc[2, "actual_wins"]
    assert out.loc[2, "won_division"] and not out.loc[1, "won_division"]
    assert out.won_division.sum() == 2


def _fake_results(n: int, p: np.ndarray, rng: np.random.Generator) -> pd.DataFrame:
    """Team-seasons whose outcomes are drawn from the stated probabilities."""
    frame = pd.DataFrame({"team": [f"t{i}" for i in range(n)]})
    for _name, (key, col, _base) in OUTCOMES.items():
        frame[key] = 100 * p
        frame[col] = rng.random(n) < p
    return frame


def test_a_calibrated_forecast_scores_near_slope_one():
    rng = np.random.default_rng(0)
    p = rng.uniform(0.02, 0.98, 4000)
    summary = calibration_summary(_fake_results(4000, p, rng)).set_index("claim")
    assert summary.slope.between(0.9, 1.1).all()
    assert (summary.skill > 0).all()
    table = reliability(_fake_results(4000, p, rng), "playoffs")
    assert (table.mean_predicted - table.observed).abs().max() < 0.05


def test_an_overconfident_forecast_scores_slope_below_one():
    """Truth is a shrunk version of what was stated: the slope must say so."""
    rng = np.random.default_rng(1)
    stated = rng.uniform(0.02, 0.98, 4000)
    truth = 1 / (1 + np.exp(-0.5 * np.log(stated / (1 - stated))))
    frame = _fake_results(4000, stated, rng)
    for _name, (_key, col, _base) in OUTCOMES.items():
        frame[col] = rng.random(4000) < truth
    summary = calibration_summary(frame).set_index("claim")
    assert (summary.slope < 0.7).all()


@needs_lake
def test_the_historical_cut_leaves_games_to_project():
    """Rewound to 1 September, a finished season has a month left and nothing after
    the cut in the training set."""
    cut = dt.date(2025, 9, 1)
    standings, remaining, teams = load_season_state(2025, as_of=cut)
    assert len(remaining) > 300
    assert standings.games.max() < 162
    assert (remaining.game_date >= pd.Timestamp(cut)).all()
    training = load_training_games(2025, as_of=cut)
    assert training.game_date.max() < pd.Timestamp(cut)
    # And the live path is untouched: no cut means the season's final standings.
    full, none_left, _ = load_season_state(2025)
    assert none_left.empty and full.games.max() == 162
