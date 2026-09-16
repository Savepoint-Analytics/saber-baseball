"""Counterfactual player simulation (Phase 5).

The perturbation arithmetic is checked exactly; the simulation is checked for the
properties that matter -- the change lands on the right player, the baseline arm is
untouched, the paired estimate has the sign the linear weights predict.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.config import settings
from src.simulation.counterfactual import (
    PROPORTIONAL,
    Perturbation,
    lever_table,
    linear_weight_delta,
    perturbed_model,
)
from src.simulation.pa_model import OUTCOMES, PAOutcomeModel

LAKE = (settings.data_dir / "saber.duckdb").resolve()
needs_lake = pytest.mark.skipif(not LAKE.exists(), reason="DuckDB lake not built")

# A realistic nine-outcome vector that sums to exactly 1: `apply` renormalises,
# so a fixture that does not sum to 1 quietly shifts every assertion below.
RATES = pd.Series([0.22, 0.08, 0.01, 0.15, 0.05, 0.005, 0.03, 0.01, 0.445], index=list(OUTCOMES))
assert RATES.sum() == pytest.approx(1.0)
LW = pd.Series([-0.27, 0.33, 0.36, 0.47, 0.76, 1.0, 1.39, 0.44, -0.27], index=list(OUTCOMES))


def test_named_absorb_moves_exactly_the_mass():
    new = Perturbation(1, "batter", {"K": -0.03}, absorb="1B").apply(RATES)
    assert new.sum() == pytest.approx(1.0)
    assert new["K"] == pytest.approx(0.19)
    assert new["1B"] == pytest.approx(0.18)
    assert new["OUT"] == pytest.approx(RATES["OUT"])


def test_proportional_absorb_keeps_the_others_in_ratio():
    new = Perturbation(1, "batter", {"K": -0.03}).apply(RATES)
    assert new.sum() == pytest.approx(1.0)
    others = [o for o in OUTCOMES if o != "K"]
    ratio = new[others] / RATES[others]
    assert ratio.std() < 1e-12  # every other outcome scaled by the same factor
    assert new["OUT"] > RATES["OUT"]


def test_perturbation_refuses_to_leave_the_simplex():
    with pytest.raises(ValueError, match="pushes"):
        Perturbation(1, "batter", {"HR": -0.05}, absorb="OUT").apply(RATES)


def test_perturbation_validates_its_inputs():
    with pytest.raises(ValueError):
        Perturbation(1, "batter", {"XX": 0.1})
    with pytest.raises(ValueError):
        Perturbation(1, "batter", {"K": -0.1}, absorb="K")
    with pytest.raises(ValueError):
        Perturbation(1, "coach", {"K": -0.1})


def test_linear_weight_delta_has_the_expected_sign_and_size():
    into_singles = Perturbation(1, "batter", {"K": -0.01}, absorb="1B")
    into_outs = Perturbation(1, "batter", {"K": -0.01}, absorb="OUT")
    assert linear_weight_delta(RATES, into_singles, LW) == pytest.approx(0.01 * (0.47 + 0.27) * 600)
    assert abs(linear_weight_delta(RATES, into_outs, LW)) < 1e-9
    # The same change on a pitcher is runs allowed, so the sign flips.
    as_pitcher = Perturbation(1, "pitcher", {"K": -0.01}, absorb="1B")
    assert linear_weight_delta(RATES, as_pitcher, LW) == pytest.approx(
        -linear_weight_delta(RATES, into_singles, LW)
    )


def test_lever_table_ranks_power_first_and_contact_to_outs_last():
    tbl = lever_table(RATES, 1, "batter", LW)
    assert tbl.iloc[0]["to"] == "HR"
    assert tbl.iloc[-1]["lever"].startswith("contact, all outs")


def test_pitcher_levers_are_reversed_so_they_read_as_improvements():
    """A pitcher's levers must be things that help him, not the batter."""
    tbl = lever_table(RATES, 1, "pitcher", LW)
    assert (tbl.runs_per_600 >= -1e-9).all()
    top = tbl.iloc[0]
    assert (top["from"], top["to"]) == ("HR", "OUT")  # give up fewer homers
    # Each named lever is worth the batter's figure *unchanged*, not negated: the
    # move is reversed (HR -> OUT rather than OUT -> HR) and linear_weight_delta
    # then flips the sign for a pitcher, so the two reversals cancel. Giving up one
    # fewer homer is worth what hitting one more is worth.
    batter = lever_table(RATES, 1, "batter", LW).set_index("lever")
    named = tbl[tbl.to != PROPORTIONAL].set_index("lever")
    np.testing.assert_allclose(
        named.runs_per_600.to_numpy(),
        batter.loc[named.index].runs_per_600.to_numpy(),
        atol=1e-9,
    )
    proportional = tbl[tbl.to == PROPORTIONAL].iloc[0]
    assert proportional.runs_per_600 > 0  # gaining strikeouts helps a pitcher


def _toy_model() -> PAOutcomeModel:
    rng = np.random.default_rng(0)
    pa = pd.DataFrame(
        {
            "batter": rng.integers(1, 4, 3000),
            "pitcher": rng.integers(10, 13, 3000),
            "outcome": rng.choice(OUTCOMES, 3000, p=RATES.to_numpy()),
            "state_before": rng.integers(0, 24, 3000),
        }
    )
    return PAOutcomeModel().fit(pa)


def test_perturbed_model_changes_one_row_and_leaves_the_original_alone():
    model = _toy_model()
    before = model.batters.copy()
    cf = perturbed_model(model, Perturbation(2, "batter", {"HR": 0.02}, absorb="OUT"))
    pd.testing.assert_frame_equal(model.batters, before)  # original untouched
    assert cf.batters.loc[2, "HR"] == pytest.approx(before.loc[2, "HR"] + 0.02)
    pd.testing.assert_series_equal(cf.batters.loc[1], before.loc[1])
    pd.testing.assert_frame_equal(cf.pitchers, model.pitchers)
    with pytest.raises(KeyError):
        perturbed_model(model, Perturbation(99, "batter", {"HR": 0.02}, absorb="OUT"))


@needs_lake
def test_simulated_counterfactual_moves_runs_the_way_the_weights_say():
    from src.simulation.counterfactual import simulate_counterfactual
    from src.simulation.transitions import load_plate_appearances

    model = PAOutcomeModel().fit(load_plate_appearances((2025,)))
    # Ohtani: eight points of strikeouts become singles. Deliberately larger than
    # anything realistic -- the point is a signal big enough to assert on in a test
    # that has to stay quick, not a plausible development target.
    p = Perturbation(660271, "batter", {"K": -0.08}, absorb="1B")
    result = simulate_counterfactual(2025, p, n_games=20, n_sims=300, seed=3, model=model)
    assert result.games == 20
    assert result.runs_for.shape == (2, 20)
    d_runs, se = result.delta_runs_for
    assert d_runs > 0
    assert d_runs > 2 * se  # the paired design makes 20 games enough to see this
    d_wp, _ = result.delta_win_prob
    assert d_wp > 0
    # Runs allowed should not move systematically -- the change is on offence.
    d_against, se_against = result.delta_runs_against
    assert abs(d_against) < 3 * se_against + 0.05


@needs_lake
def test_common_random_numbers_actually_pair_the_arms():
    """The paired design is the reason a 20-game run says anything, so check it.

    Both arms run from the same per-game seed. They diverge -- one different
    outcome draw and the streams separate -- so the correlation is not 1, but it
    is high enough to cut the standard error of the difference several-fold.
    """
    from src.simulation.counterfactual import simulate_counterfactual
    from src.simulation.transitions import load_plate_appearances

    model = PAOutcomeModel().fit(load_plate_appearances((2025,)))
    p = Perturbation(660271, "batter", {"K": -0.08}, absorb="1B")
    result = simulate_counterfactual(2025, p, n_games=20, n_sims=300, seed=11, model=model)
    base, cf = result.runs_for
    assert np.corrcoef(base, cf)[0, 1] > 0.85
    paired_sd = (cf - base).std(ddof=1)
    independent_sd = np.hypot(base.std(ddof=1), cf.std(ddof=1))
    assert paired_sd < 0.6 * independent_sd


@needs_lake
def test_a_pitcher_counterfactual_moves_runs_allowed():
    from src.simulation.counterfactual import player_games, simulate_counterfactual
    from src.simulation.transitions import load_plate_appearances

    model = PAOutcomeModel().fit(load_plate_appearances((2025,)))
    # Skubal: five points of hits allowed become strikeouts.
    p = Perturbation(669373, "pitcher", {"1B": -0.05}, absorb="K")
    assert len(player_games(2025, 669373, "pitcher")) >= 25  # his starts
    result = simulate_counterfactual(2025, p, n_games=20, n_sims=300, seed=5, model=model)
    d_against, _ = result.delta_runs_against
    assert d_against < 0
    assert result.delta_win_prob[0] > 0
