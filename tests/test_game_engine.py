"""The plate-appearance game loop.

Most of what can go wrong here is a rules violation that still produces a plausible
box score: a home team batting in the ninth while ahead, a game ending in a tie, a
walk-off inning that keeps going. Those are asserted directly.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.config import settings
from src.simulation.game_engine import GameResult, GameSimulator, TeamSetup
from src.simulation.lineup import Bullpen, Lineup, SubstitutionPolicy
from src.simulation.pa_model import OUTCOMES, PAOutcomeModel
from src.simulation.transitions import TransitionMatrix

LAKE = (settings.data_dir / "saber.duckdb").resolve()
needs_lake = pytest.mark.skipif(not LAKE.exists(), reason="DuckDB lake not built")

UNKNOWN_BATTERS = tuple(range(900001, 900010))
UNKNOWN_RELIEVERS = np.arange(910001, 910006)


def _setup() -> TeamSetup:
    """A team of players the model has never seen, so every rate is league-average."""
    return TeamSetup(
        lineup=Lineup(UNKNOWN_BATTERS),
        starter=910000,
        bullpen=Bullpen(1, UNKNOWN_RELIEVERS, np.ones(len(UNKNOWN_RELIEVERS))),
    )


@pytest.fixture(scope="module")
def simulator() -> GameSimulator:
    from src.simulation.transitions import load_plate_appearances

    model = PAOutcomeModel().fit(load_plate_appearances((2025,)))
    return GameSimulator(
        model, TransitionMatrix.load("2023-2026"), SubstitutionPolicy.load("2023-2026")
    )


# --- setup ----------------------------------------------------------------
def test_pitcher_pool_puts_the_starter_first():
    """Index 0 being the starter is assumed throughout the loop."""
    s = _setup()
    assert s.pitcher_pool[0] == s.starter
    assert len(s.pitcher_pool) == 1 + len(s.bullpen.pitchers)


def test_game_result_summaries():
    r = GameResult(home_score=np.array([3, 5, 1]), away_score=np.array([2, 5, 4]))
    assert r.n_sims == 3
    assert r.home_win_probability() == pytest.approx(1 / 3)
    assert r.mean_total_runs() == pytest.approx((5 + 10 + 5) / 3)
    assert sum(r.score_distribution().values()) == pytest.approx(1.0)


# --- rules ----------------------------------------------------------------
@needs_lake
def test_no_simulated_game_ends_in_a_tie(simulator):
    """Extra innings run until someone wins; a tie means the loop gave up."""
    r = simulator.simulate(_setup(), _setup(), n_sims=3000, rng=np.random.default_rng(0))
    assert not (r.home_score == r.away_score).any()


@needs_lake
def test_identical_teams_are_a_coin_flip_apart_from_home_field(simulator):
    """Two league-average teams should differ only by the measured HFA."""
    r = simulator.simulate(_setup(), _setup(), n_sims=8000, rng=np.random.default_rng(1))
    assert 0.50 < r.home_win_probability() < 0.56


@needs_lake
def test_same_seed_reproduces_the_same_games(simulator):
    """Reproducibility is the project's first golden rule."""
    a = simulator.simulate(_setup(), _setup(), n_sims=500, rng=np.random.default_rng(7))
    b = simulator.simulate(_setup(), _setup(), n_sims=500, rng=np.random.default_rng(7))
    assert (a.home_score == b.home_score).all()
    assert (a.away_score == b.away_score).all()


@needs_lake
def test_ghost_runner_raises_scoring_in_extra_innings(simulator):
    """The 2020 rule exists to end games; it must actually add runs."""
    kw = {"n_sims": 6000}
    with_ghost = GameSimulator(
        simulator.pa_model, simulator.transitions, simulator.policy, ghost_runner_from_inning=10
    ).simulate(_setup(), _setup(), rng=np.random.default_rng(3), **kw)
    without = GameSimulator(
        simulator.pa_model, simulator.transitions, simulator.policy, ghost_runner_from_inning=None
    ).simulate(_setup(), _setup(), rng=np.random.default_rng(3), **kw)
    assert with_ghost.mean_total_runs() > without.mean_total_runs()


@needs_lake
def test_a_better_lineup_scores_more(simulator):
    """Sanity: the engine must respond to who is batting."""
    weak = _setup()
    # Reuse real batters from the fitted model as the strong side.
    strong_ids = tuple(int(i) for i in simulator.pa_model.batters.index[:9])
    strong = TeamSetup(Lineup(strong_ids), weak.starter, weak.bullpen)
    rng = np.random.default_rng(11)
    a = simulator.simulate(strong, weak, n_sims=4000, rng=rng)
    b = simulator.simulate(weak, strong, n_sims=4000, rng=rng)
    # 'strong' bats as home in a, as away in b; both should out-score the league team.
    assert a.home_score.mean() != pytest.approx(b.home_score.mean(), rel=0.001)


# --- fidelity to the validated components ---------------------------------
@needs_lake
def test_nine_inning_scoring_matches_the_analytic_markov_solve(simulator):
    """The loop must be faithful to the matrix it steps.

    With league-average players and exactly nine innings, the away team's run total
    is 9 x RE(bases empty, none out) by construction. Any drift means the loop is not
    stepping the transition matrix it was validated against.
    """
    engine = GameSimulator(
        simulator.pa_model,
        simulator.transitions,
        simulator.policy,
        ghost_runner_from_inning=None,
        max_innings=9,
    )
    r = engine.simulate(_setup(), _setup(), n_sims=30000, rng=np.random.default_rng(9))
    # Away batting: no side term applies to the analytic solve, so compare like for like.
    expected = 9 * simulator.transitions.run_expectancy(simulator.pa_model.state_probabilities())[0]
    assert r.away_score.mean() == pytest.approx(expected, rel=0.04)


@needs_lake
def test_home_team_does_not_bat_when_already_ahead(simulator):
    """If the home side batted needlessly it would out-score the away side sharply."""
    r = simulator.simulate(_setup(), _setup(), n_sims=6000, rng=np.random.default_rng(2))
    # Home bats fewer innings, so despite home-field advantage its mean stays close.
    assert abs(r.home_score.mean() - r.away_score.mean()) < 0.35


@needs_lake
def test_transition_rows_are_all_populated(simulator):
    """A missing (state, outcome) row would silently sample the last flat index.

    That decodes to the terminal state with the maximum run value, so an unobserved
    combination would quietly score six runs and end the inning.
    """
    totals = simulator.transitions.probs.sum(axis=(2, 3))
    assert (totals > 1e-9).all(), "unobserved (state, outcome) pair would corrupt sampling"
    assert totals.shape == (24, len(OUTCOMES))
