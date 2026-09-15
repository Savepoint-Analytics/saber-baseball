"""Tests for the baseline models and the backtest harness.

The tests that matter most here are the *causality* ones: a backtest that leaks the
future is worse than no backtest, because it reports a number that looks like skill.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.models.backtest import (
    accuracy,
    brier_score,
    calibration_table,
    expected_calibration_error,
    log_loss,
    walk_forward,
)
from src.models.baselines import (
    Baseline,
    EloBaseline,
    HomeFieldBaseline,
    Log5Baseline,
    PythagoreanBaseline,
    elo_expectation,
    elo_update,
    home_field_prior,
    log5,
    pythagorean_expectation,
)

ALL_MODELS = (HomeFieldBaseline, Log5Baseline, PythagoreanBaseline, EloBaseline)


def _games(seasons=(2020, 2021, 2022), teams=(1, 2, 3, 4), seed=0) -> pd.DataFrame:
    """Synthetic schedule where lower team_id is genuinely stronger."""
    rng = np.random.default_rng(seed)
    rows = []
    pk = 0
    for season in seasons:
        for day in range(30):
            for home in teams:
                for away in teams:
                    if home == away:
                        continue
                    pk += 1
                    p = 0.5 + 0.06 * (away - home)  # lower id => stronger
                    hw = int(rng.random() < p)
                    rows.append(
                        {
                            "game_pk": pk,
                            "game_date": pd.Timestamp(f"{season}-04-01") + pd.Timedelta(days=day),
                            "season": season,
                            "home_team_id": home,
                            "away_team_id": away,
                            "home_score": 5 + hw,
                            "away_score": 5 + (1 - hw),
                            "home_win": float(hw),
                        }
                    )
    return pd.DataFrame(rows)


# --- primitives ------------------------------------------------------------
def test_pythagorean_is_symmetric_and_bounded():
    assert pythagorean_expectation(700, 700) == pytest.approx(0.5)
    assert pythagorean_expectation(900, 600) > 0.5
    assert pythagorean_expectation(0, 0) == 0.5  # no games: no information


def test_log5_recovers_inputs_against_an_average_team():
    """Log5 against a .500 opponent must return the team's own rate."""
    for p in (0.3, 0.5, 0.62):
        assert log5(p, 0.5) == pytest.approx(p)


def test_elo_update_is_zero_sum_and_directional():
    a, b = elo_update(1500, 1500, a_won=True, k=20)
    assert a > 1500 > b
    assert (a - 1500) + (b - 1500) == pytest.approx(0.0)  # zero sum


def test_elo_home_advantage_raises_home_win_probability():
    assert elo_expectation(1500, 1500, home_adv=0) == pytest.approx(0.5)
    assert elo_expectation(1500, 1500, home_adv=24) > 0.5


def test_home_field_prior_smooths_toward_half():
    assert home_field_prior(0, 0) == pytest.approx(0.5)  # no data -> no claim
    assert 0.5 < home_field_prior(60, 100) < 0.6


# --- metrics ---------------------------------------------------------------
def test_log_loss_matches_hand_computed_value():
    p = np.array([0.9, 0.1])
    y = np.array([1.0, 0.0])
    assert log_loss(p, y) == pytest.approx(-np.log(0.9))


def test_log_loss_is_finite_on_a_confident_miss():
    """Unclipped, this is infinity and every average downstream becomes nan."""
    assert np.isfinite(log_loss(np.array([0.0]), np.array([1.0])))


def test_brier_and_accuracy_are_sane():
    p, y = np.array([1.0, 0.0]), np.array([1.0, 0.0])
    assert brier_score(p, y) == pytest.approx(0.0)
    assert accuracy(p, y) == pytest.approx(1.0)


def test_calibration_table_reports_observed_rates_per_bin():
    p = np.array([0.15] * 100 + [0.85] * 100)
    y = np.array([0.0] * 100 + [1.0] * 100)
    table = calibration_table(p, y, bins=10)
    assert table["n"].sum() == 200
    assert expected_calibration_error(p, y) == pytest.approx(0.15, abs=0.01)


# --- model contract --------------------------------------------------------
@pytest.mark.parametrize("factory", ALL_MODELS)
def test_models_satisfy_the_baseline_protocol(factory):
    assert isinstance(factory(), Baseline)


@pytest.mark.parametrize("factory", ALL_MODELS)
def test_predictions_are_probabilities_one_per_row(factory):
    games = _games()
    model = factory().fit(games[games.season < 2022])
    probs = model.predict_proba(games[games.season == 2022])
    assert len(probs) == (games.season == 2022).sum()
    assert np.all((probs > 0) & (probs < 1))


@pytest.mark.parametrize("factory", ALL_MODELS)
def test_missing_columns_fail_loudly(factory):
    with pytest.raises(ValueError, match="missing required columns"):
        factory().fit(pd.DataFrame({"season": [2024]}))


def test_prior_season_models_handle_unseen_teams():
    """An expansion team has no prior season; it must get .500, not a KeyError."""
    games = _games()
    model = PythagoreanBaseline().fit(games[games.season < 2022])
    unseen = games[games.season == 2022].head(1).assign(home_team_id=99, away_team_id=98)
    assert model.predict_proba(unseen)[0] == pytest.approx(0.5, abs=0.05)


def test_stronger_team_gets_the_higher_probability():
    """Sanity: the models must actually learn who is good."""
    games = _games()
    train, test = games[games.season < 2022], games[games.season == 2022]
    for factory in (Log5Baseline, PythagoreanBaseline, EloBaseline):
        probs = factory().fit(train).predict_proba(test)
        strong_at_home = (test.home_team_id == 1) & (test.away_team_id == 4)
        weak_at_home = (test.home_team_id == 4) & (test.away_team_id == 1)
        assert probs[strong_at_home.to_numpy()].mean() > probs[weak_at_home.to_numpy()].mean()


# --- causality -------------------------------------------------------------
def test_elo_does_not_use_a_games_own_result():
    """Flipping one game's outcome must not change that game's own prediction."""
    games = _games()
    model_a = EloBaseline().fit(games[games.season < 2022])
    test = games[games.season == 2022].reset_index(drop=True)
    baseline_pred = model_a.predict_proba(test)[10]

    flipped = test.copy()
    flipped.loc[10, "home_win"] = 1.0 - flipped.loc[10, "home_win"]
    model_b = EloBaseline().fit(games[games.season < 2022])
    assert model_b.predict_proba(flipped)[10] == pytest.approx(baseline_pred)


def test_elo_preserves_caller_row_order():
    """predict_proba sorts internally by date; it must hand back the original order."""
    games = _games()
    test = games[games.season == 2022]
    shuffled = test.sample(frac=1.0, random_state=7)
    model = EloBaseline().fit(games[games.season < 2022])
    p_ordered = pd.Series(model.predict_proba(test), index=test.index)
    p_shuffled = pd.Series(model.predict_proba(shuffled), index=shuffled.index)
    pd.testing.assert_series_equal(p_ordered, p_shuffled.reindex(test.index))


def test_walk_forward_never_trains_on_the_test_season():
    """The core anti-leakage guarantee of the harness."""
    games = _games(seasons=(2019, 2020, 2021, 2022))
    seen: list[tuple[int, int]] = []

    class Spy(HomeFieldBaseline):
        def fit(self, g):
            seen.append((int(g.season.max()), -1))
            return super().fit(g)

        def predict_proba(self, g):
            seen[-1] = (seen[-1][0], int(g.season.min()))
            return super().predict_proba(g)

    walk_forward(games, models={"spy": Spy}, min_train_seasons=2)
    assert seen, "harness never ran a fold"
    for max_train_season, test_season in seen:
        assert max_train_season < test_season


def test_walk_forward_returns_metrics_and_predictions_for_every_fold():
    games = _games(seasons=(2019, 2020, 2021, 2022))
    metrics, preds = walk_forward(games, min_train_seasons=2)
    # Derived from the registry, not hard-coded: adding a model family should not
    # fail a test about fold coverage. This assertion has now broken twice for that
    # reason -- once when Bradley-Terry was registered, once for the published marts.
    from src.models.backtest import MODELS

    assert set(metrics.model.unique()) == set(MODELS)
    assert len(preds) == len(metrics.model.unique()) * (games.season >= 2021).sum()
    assert metrics.log_loss.between(0, 2).all()
