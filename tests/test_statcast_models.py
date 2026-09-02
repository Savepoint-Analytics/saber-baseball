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
    _BULLPEN_DIFF_COLUMNS,
    _CONTEXT_COLUMNS,
    _DIFF_COLUMNS,
    _STARTER_DIFF_COLUMNS,
    EloContextLogistic,
    EloStarterBullpenContextLogistic,
    EloStarterBullpenLogistic,
    EloStarterLogistic,
    EloStatcastLogistic,
    StarterOnlyLogistic,
    StatcastGBM,
    StatcastGBMNoElo,
)

FORM_MODELS = (StatcastGBM, StatcastGBMNoElo, EloStatcastLogistic)
STARTER_MODELS = (EloStarterLogistic, StarterOnlyLogistic)
CONTEXT_MODELS = (EloStarterBullpenLogistic, EloStarterBullpenContextLogistic, EloContextLogistic)


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
                    for c in _DIFF_COLUMNS + _STARTER_DIFF_COLUMNS + _BULLPEN_DIFF_COLUMNS:
                        row[f"home_{c}"] = (5 - home) * 0.1 + rng.normal(0, 0.02)
                        row[f"away_{c}"] = (5 - away) * 0.1 + rng.normal(0, 0.02)
                    row["home_starts_in_window"] = 10
                    row["away_starts_in_window"] = 10
                    for c in _CONTEXT_COLUMNS:
                        row[c] = float(rng.normal(1.0, 0.1))
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


# --- starting-pitcher models ----------------------------------------------
@pytest.mark.parametrize("factory", STARTER_MODELS)
def test_starter_models_satisfy_the_protocol_and_predict(factory):
    g = _games()
    model = factory().fit(g[g.season < 2022])
    p = model.predict_proba(g[g.season == 2022])
    assert isinstance(model, Baseline)
    assert len(p) == (g.season == 2022).sum()
    assert np.all((p > 0) & (p < 1))


@pytest.mark.parametrize("factory", STARTER_MODELS)
def test_missing_starter_columns_name_the_right_loader_flag(factory):
    """The error must say with_starters=True, not the team-form flag."""
    g = _games().drop(columns=[f"home_{c}" for c in _STARTER_DIFF_COLUMNS])
    with pytest.raises(ValueError, match="with_starters=True"):
        factory().fit(g)


@pytest.mark.parametrize("factory", STARTER_MODELS)
def test_starter_models_are_causal(factory):
    """Flipping a game's own result must not move its own prediction."""
    g = _games()
    train, test = g[g.season < 2022], g[g.season == 2022].reset_index(drop=True)
    before = factory().fit(train).predict_proba(test)[7]
    flipped = test.copy()
    flipped.loc[7, "home_win"] = 1.0 - flipped.loc[7, "home_win"]
    assert factory().fit(train).predict_proba(flipped)[7] == pytest.approx(before)


def test_starter_feature_set_actually_includes_the_tto_columns():
    """The TTO splits are the reason this model exists; guard against silent drops."""
    cols = EloStarterLogistic().diff_columns
    assert "sp_form_xwoba_allowed_tto1" in cols
    assert "sp_form_xwoba_allowed_tto3plus" in cols
    assert "sp_form_tto_penalty" in cols


def test_starter_only_model_excludes_team_form():
    """StarterOnlyLogistic isolates the pitcher contribution; team form must be out."""
    cols = set(StarterOnlyLogistic().diff_columns)
    assert cols == set(_STARTER_DIFF_COLUMNS)
    assert not cols & set(_DIFF_COLUMNS)


# --- bullpen + game-context models ----------------------------------------
@pytest.mark.parametrize("factory", CONTEXT_MODELS)
def test_bullpen_and_context_models_predict(factory):
    g = _games()
    p = factory().fit(g[g.season < 2022]).predict_proba(g[g.season == 2022])
    assert len(p) == (g.season == 2022).sum()
    assert np.all((p > 0) & (p < 1))


def test_context_columns_actually_reach_the_design_matrix():
    """Regression test for a silent bug.

    The context loop was once missing from ``_design``. Nothing raised: the model
    fitted, predicted, and scored — it simply ignored park and weather entirely, and
    produced predictions byte-identical to the model without them. That is the worst
    kind of defect here, because it yields a confident and completely wrong answer to
    "do these features help?". Assert the columns are really used.
    """
    g = _games()
    with_ctx = EloStarterBullpenContextLogistic().fit(g[g.season < 2022])
    without = EloStarterBullpenLogistic().fit(g[g.season < 2022])

    assert set(_CONTEXT_COLUMNS) <= set(with_ctx.feature_names)
    assert not set(_CONTEXT_COLUMNS) & set(without.feature_names)
    assert len(with_ctx.feature_names) == len(without.feature_names) + len(_CONTEXT_COLUMNS)

    test = g[g.season == 2022]
    assert not np.allclose(with_ctx.predict_proba(test), without.predict_proba(test))


def test_missing_context_columns_name_the_right_loader_flag():
    g = _games().drop(columns=list(_CONTEXT_COLUMNS))
    with pytest.raises(ValueError, match="with_context=True"):
        EloContextLogistic().fit(g)


def test_context_enters_raw_not_differenced():
    """Both teams share the park and the weather; differencing them would be zero."""
    g = _games()
    m = EloContextLogistic().fit(g[g.season < 2022])
    assert "park_factor" in m.feature_names
    assert "diff_park_factor" not in m.feature_names
