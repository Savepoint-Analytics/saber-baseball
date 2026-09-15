"""PA outcome model and base-out transition matrix (Phases 4-5).

The properties asserted here are the ones a simulation silently violates rather than
crashes on: probabilities that do not sum to 1, a shrinkage constant that does not
shrink, an absorbing state that is not absorbing. Each produces plausible-looking
runs and a wrong answer.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.config import settings
from src.simulation.pa_model import OUTCOMES, PAOutcomeModel, _regression_constant
from src.simulation.transitions import (
    MAX_RUNS,
    N_LIVE_STATES,
    TERMINAL_STATE,
    TransitionMatrix,
)

LAKE = (settings.data_dir / "saber.duckdb").resolve()
needs_lake = pytest.mark.skipif(not LAKE.exists(), reason="DuckDB lake not built")


def _pa_frame(n=4000, seed=0) -> pd.DataFrame:
    """Synthetic PAs: batter 1 is good, batter 2 is poor, batter 3 has 5 PAs."""
    rng = np.random.default_rng(seed)
    rows = []
    profiles = {
        1: [0.15, 0.12, 0.01, 0.20, 0.06, 0.01, 0.06, 0.01, 0.38],
        2: [0.32, 0.05, 0.01, 0.10, 0.03, 0.00, 0.02, 0.01, 0.46],
    }
    for batter, probs in profiles.items():
        for _ in range(n):
            rows.append(
                {
                    "batter": batter,
                    "pitcher": rng.integers(10, 14),
                    "outcome": rng.choice(OUTCOMES, p=probs),
                    "state_before": int(rng.integers(0, 24)),
                }
            )
    for _ in range(5):  # tiny-sample batter: must be shrunk hard
        rows.append({"batter": 3, "pitcher": 10, "outcome": "HR", "state_before": 0})
    return pd.DataFrame(rows)


# --- PA outcome model -----------------------------------------------------
def test_outcome_order_is_stable():
    """Vectors and the transition matrix are indexed by this order."""
    assert OUTCOMES[0] == "K" and OUTCOMES[-1] == "OUT"
    assert len(OUTCOMES) == len(set(OUTCOMES)) == 9


def test_league_rates_form_a_distribution():
    m = PAOutcomeModel().fit(_pa_frame())
    assert m.league_probabilities().sum() == pytest.approx(1.0)
    assert (m.league_probabilities() >= 0).all()


def test_probabilities_sum_to_one_for_any_matchup():
    m = PAOutcomeModel().fit(_pa_frame())
    for b in (1, 2, 3, None, 999):
        assert m.probabilities(b, 10).sum() == pytest.approx(1.0)


def test_shrinkage_pulls_a_five_pa_batter_most_of_the_way_to_league():
    """Batter 3 is 5-for-5 on home runs. Believing that would break the simulation.

    Asserted as a fraction of the distance travelled from the raw rate toward league,
    not as an absolute threshold: the regression constant is estimated from the data,
    so on a synthetic fixture with three wildly different batters it is legitimately
    smaller than on a real season.
    """
    m = PAOutcomeModel().fit(_pa_frame())
    hr = OUTCOMES.index("HR")
    league_hr = m.league_probabilities()[hr]
    shrunk = m.batters.loc[3].to_numpy()[hr]

    travelled = (1.0 - shrunk) / (1.0 - league_hr)
    assert travelled > 0.8, f"5 PA of home runs only regressed {travelled:.0%} to league"
    assert shrunk > league_hr, "shrinkage overshot past the league rate"


@needs_lake
def test_regression_constants_match_known_sabermetric_values():
    """The constants are estimated, not coded in, so they are worth checking.

    Published reference points are roughly 60-70 PA for strikeout rate and 110-120
    for walk rate. Doubles and reach-on-error should regress almost entirely: both
    are dominated by park and luck rather than repeatable skill, and an estimator
    that claimed otherwise would be inventing talent.
    """
    from src.simulation.transitions import load_plate_appearances

    m = PAOutcomeModel().fit(load_plate_appearances((2023, 2024, 2025, 2026)))
    assert 30 < m.batter_k["K"] < 110
    assert 80 < m.batter_k["BB"] < 200
    assert m.batter_k["2B"] > 1000
    assert m.batter_k["ROE"] > 1000


def test_regression_constant_is_huge_when_spread_is_pure_noise():
    """No real talent spread => trust the league rate, do not divide by ~0."""
    rates = np.array([0.3, 0.3, 0.3])
    n = np.array([100.0, 100.0, 100.0])
    assert _regression_constant(rates, n, 0.3) >= 1e5


def test_odds_ratio_of_two_average_players_returns_the_league_rate():
    """The defining property of the method: average vs average is league."""
    m = PAOutcomeModel().fit(_pa_frame())
    lg = m.league_probabilities()
    m.batters.loc[99] = lg
    m.pitchers.loc[99] = lg
    assert m.probabilities(99, 99) == pytest.approx(lg)


def test_a_good_batter_beats_a_poor_one_against_the_same_pitcher():
    m = PAOutcomeModel().fit(_pa_frame())
    k = OUTCOMES.index("K")
    assert m.probabilities(1, 10)[k] < m.probabilities(2, 10)[k]


def test_unknown_players_fall_back_to_league_rather_than_raising():
    """Simulating a future season means pricing call-ups with no history."""
    m = PAOutcomeModel().fit(_pa_frame())
    assert m.probabilities(123456, 654321) == pytest.approx(m.league_probabilities())


def test_probability_matrix_matches_the_scalar_path():
    m = PAOutcomeModel().fit(_pa_frame())
    batters, pitchers = np.array([1, 2, 3]), np.array([10, 11, 12])
    matrix = m.probability_matrix(batters, pitchers)
    for i, (b, p) in enumerate(zip(batters, pitchers, strict=True)):
        assert matrix[i] == pytest.approx(m.probabilities(int(b), int(p)))


def test_state_term_changes_the_distribution():
    """Outcome rates depend on base-out state; ignoring it biases run expectancy."""
    m = PAOutcomeModel().fit(_pa_frame())
    assert not m.states.empty
    assert m.state_probabilities().shape == (24, len(OUTCOMES))
    assert not np.allclose(m.probabilities(1, 10, state=0), m.probabilities(1, 10, state=7))


# --- transition matrix ----------------------------------------------------
@needs_lake
def test_matrix_shape_and_normalisation():
    tm = TransitionMatrix.load("2023-2026")
    assert tm.probs.shape == (N_LIVE_STATES, len(OUTCOMES), TERMINAL_STATE + 1, MAX_RUNS + 1)
    totals = tm.probs.sum(axis=(2, 3))
    assert np.allclose(totals[totals > 0], 1.0)


@needs_lake
def test_run_expectancy_accepts_both_shapes_and_agrees_when_equivalent():
    tm = TransitionMatrix.load("2023-2026")
    lg = np.full(len(OUTCOMES), 1 / len(OUTCOMES))
    assert tm.run_expectancy(lg) == pytest.approx(tm.run_expectancy(np.tile(lg, (24, 1))))


@needs_lake
def test_run_expectancy_falls_as_outs_rise():
    from src.simulation.transitions import load_plate_appearances

    tm = TransitionMatrix.load("2023-2026")
    m = PAOutcomeModel().fit(load_plate_appearances((2025,)))
    re = tm.run_expectancy(m.state_probabilities())
    for bases in range(8):
        assert re[bases] > re[8 + bases] > re[16 + bases], f"bases={bases}"


@needs_lake
def test_home_runs_never_reach_the_absorbing_state():
    """A home run records no out; if the matrix says otherwise it is corrupt."""
    tm = TransitionMatrix.load("2023-2026")
    hr = OUTCOMES.index("HR")
    assert tm.probs[:, hr, TERMINAL_STATE, :].sum() == pytest.approx(0.0)


@needs_lake
def test_bad_outcome_probability_shape_is_rejected():
    tm = TransitionMatrix.load("2023-2026")
    with pytest.raises(ValueError, match="expected shape"):
        tm.run_expectancy(np.ones(3))


@needs_lake
def test_analytic_run_expectancy_tracks_the_measured_matrix():
    """The engine's headline validation, pinned so a regression cannot pass quietly.

    The analytic solve uses only the transition matrix; the measured RE24 is computed
    independently in dbt. Agreement means the matrix encodes real baseball. The
    residual bias is expected and understood: the chain assumes a league-average
    pitcher, while in reality the pitcher who has loaded the bases is a worse one
    (pitcher quality correlates +0.79 with state run expectancy). The game loop,
    which simulates specific pitchers, does not make that assumption.
    """
    from src.simulation.transitions import empirical_run_expectancy, load_plate_appearances

    tm = TransitionMatrix.load("2023-2026")
    m = PAOutcomeModel().fit(load_plate_appearances((2023, 2024, 2025, 2026)))
    analytic = tm.run_expectancy(m.state_probabilities())
    measured = empirical_run_expectancy("2023-2026").set_index("state").run_expectancy

    diffs = np.array([analytic[s] - measured[s] for s in range(N_LIVE_STATES)])
    assert np.abs(diffs).mean() < 0.05, "analytic RE24 has drifted from the measured matrix"
    assert -0.05 < diffs.mean() < 0.0, "bias should be small and negative (pitcher selection)"


@needs_lake
def test_expected_runs_per_game_is_within_two_percent_of_the_league():
    """Validate against league run scoring — the gate before any game loop."""
    from src.simulation.transitions import load_plate_appearances

    tm = TransitionMatrix.load("2023-2026")
    m = PAOutcomeModel().fit(load_plate_appearances((2023, 2024, 2025, 2026)))
    modelled = tm.expected_runs_per_game(m.state_probabilities())
    assert 4.35 < modelled < 4.55, f"run scoring off: {modelled:.3f}"
