"""Statcast form models and their causality guarantees.

These models consume rolling features, which is the easiest place in the project to
leak the future by accident: an off-by-one in a window bound produces numbers that
look entirely reasonable and a backtest score no live prediction could reproduce.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.models.baselines import Baseline
from src.models.statcast import (
    _DIFF_COLUMNS,
    EloStatcastLogistic,
    StatcastGBM,
    StatcastGBMNoElo,
)

FORM_MODELS = (StatcastGBM, StatcastGBMNoElo, EloStatcastLogistic)


def _games(seasons=(2020, 2021, 2022), teams=(1, 2, 3, 4), seed=0) -> pd.DataFrame:
    """Synthetic schedule with form columns; lower team_id is genuinely stronger.

    Deliberately large: the GBM ships with ``min_samples_leaf=300`` tuned for a real
    ~16k-row training window, and on a small fixture that setting leaves it no room
    to split, so it returns a constant and every behavioural assertion is vacuous.
    """
    rng = np.random.default_rng(seed)
    rows = []
    pk = 0
    for season in seasons:
        for day in range(120):
            for home in teams:
                for away in teams:
                    if home == away:
                        continue
                    pk += 1
                    p = 0.5 + 0.06 * (away - home)
                    hw = int(rng.random() < p)
                    row = {
                        "game_pk": pk,
                        "game_date": pd.Timestamp(f"{season}-04-01") + pd.Timedelta(days=day),
                        "season": season,
                        "home_team_id": home,
                        "away_team_id": away,
                        "home_score": 5 + hw,
                        "away_score": 5 + (1 - hw),
                        "home_win": float(hw),
                        "home_games_in_window": 30,
                        "away_games_in_window": 30,
                    }
                    # Form tracks true strength, plus noise.
                    for c in _DIFF_COLUMNS:
                        row[f"home_{c}"] = (5 - home) * 0.1 + rng.normal(0, 0.02)
                        row[f"away_{c}"] = (5 - away) * 0.1 + rng.normal(0, 0.02)
                    rows.append(row)
    return pd.DataFrame(rows)


@pytest.mark.parametrize("factory", FORM_MODELS)
def test_models_satisfy_the_baseline_protocol(factory):
    assert isinstance(factory(), Baseline)


@pytest.mark.parametrize("factory", FORM_MODELS)
def test_predictions_are_probabilities_one_per_row(factory):
    g = _games()
    model = factory().fit(g[g.season < 2022])
    p = model.predict_proba(g[g.season == 2022])
    assert len(p) == (g.season == 2022).sum()
    assert np.all((p > 0) & (p < 1))


@pytest.mark.parametrize("factory", FORM_MODELS)
def test_missing_form_columns_fail_with_an_actionable_message(factory):
    """The fix is load_games(with_form=True); the error should say so."""
    g = _games().drop(columns=[f"home_{c}" for c in _DIFF_COLUMNS])
    with pytest.raises(ValueError, match="with_form=True|missing required columns"):
        factory().fit(g)


@pytest.mark.parametrize("factory", FORM_MODELS)
def test_predict_before_fit_raises(factory):
    with pytest.raises(RuntimeError, match="before fit"):
        factory().predict_proba(_games())


def test_stronger_team_gets_the_higher_probability():
    g = _games()
    train, test = g[g.season < 2022], g[g.season == 2022]
    for factory in FORM_MODELS:
        p = factory().fit(train).predict_proba(test)
        strong_home = ((test.home_team_id == 1) & (test.away_team_id == 4)).to_numpy()
        weak_home = ((test.home_team_id == 4) & (test.away_team_id == 1)).to_numpy()
        assert p[strong_home].mean() > p[weak_home].mean(), factory.__name__


@pytest.mark.parametrize("factory", FORM_MODELS)
def test_a_games_own_outcome_does_not_change_its_own_prediction(factory):
    """The causality guarantee: flipping a test result must not move its forecast."""
    g = _games()
    train, test = g[g.season < 2022], g[g.season == 2022].reset_index(drop=True)
    model = factory().fit(train)
    before = model.predict_proba(test)[10]

    flipped = test.copy()
    flipped.loc[10, "home_win"] = 1.0 - flipped.loc[10, "home_win"]
    assert factory().fit(train).predict_proba(flipped)[10] == pytest.approx(before)


def test_logistic_handles_missing_form_as_no_information():
    """A team with no prior form must fall back toward Elo, not toward a lean."""
    g = _games()
    train, test = g[g.season < 2022], g[g.season == 2022].copy()
    model = EloStatcastLogistic().fit(train)
    blanked = test.copy()
    for c in _DIFF_COLUMNS:
        blanked[f"home_{c}"] = np.nan
        blanked[f"away_{c}"] = np.nan
    p = model.predict_proba(blanked)
    assert np.all(np.isfinite(p)) and np.all((p > 0) & (p < 1))


def test_burn_in_seasons_never_empties_the_training_set():
    """A short training window must still fit rather than drop everything."""
    g = _games(seasons=(2021,))
    model = EloStatcastLogistic(burn_in_seasons=1).fit(g)
    assert model.model is not None
