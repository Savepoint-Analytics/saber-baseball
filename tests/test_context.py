"""Per-game run environment: park and weather as outcome multipliers.

The calibration is the part worth guarding. It converts a *run* multiplier into
*outcome-rate* multipliers by solving against the same analytic Markov solve used to
validate the transition matrix, so a silent drift there would make every simulated
park wrong in a way no other test would notice.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.config import settings
from src.simulation.context import (
    MAX_MULTIPLIER,
    MIN_MULTIPLIER,
    RunEnvironment,
    load_direction,
    load_game_environments,
)
from src.simulation.pa_model import OUTCOMES, PAOutcomeModel
from src.simulation.transitions import TransitionMatrix

LAKE = (settings.data_dir / "saber.duckdb").resolve()
needs_lake = pytest.mark.skipif(not LAKE.exists(), reason="DuckDB lake not built")


@pytest.fixture(scope="module")
def calibrated():
    from src.simulation.transitions import load_plate_appearances

    model = PAOutcomeModel().fit(load_plate_appearances((2023, 2024, 2025, 2026)))
    tm = TransitionMatrix.load("2023-2026")
    return model, tm, RunEnvironment.calibrate(model, tm)


# --- direction ------------------------------------------------------------
@needs_lake
def test_direction_is_centred_and_covers_every_outcome():
    d = load_direction("2023-2026")
    assert d.shape == (len(OUTCOMES),)
    assert d.mean() == pytest.approx(0.0, abs=1e-12)


@needs_lake
def test_direction_matches_the_physics():
    """A richer environment turns strikeouts into extra-base hits, not the reverse."""
    d = dict(zip(OUTCOMES, load_direction("2023-2026"), strict=True))
    assert d["HR"] > 0 and d["2B"] > 0 and d["3B"] > 0
    assert d["K"] < 0
    assert d["3B"] > d["1B"], "triples should respond more than singles"


# --- calibration ----------------------------------------------------------
@needs_lake
def test_calibration_hits_its_target_run_multipliers(calibrated):
    """The whole point: asking for 1.27x runs must deliver 1.27x runs."""
    model, tm, env = calibrated
    base_rates = model.state_probabilities()
    baseline = tm.run_expectancy(base_rates)[0]
    for target in (0.85, 1.0, 1.15, 1.27):
        adjusted = base_rates * env.outcome_multiplier(target)
        adjusted = adjusted / adjusted.sum(axis=1, keepdims=True)
        assert tm.run_expectancy(adjusted)[0] / baseline == pytest.approx(target, abs=1e-3)


@needs_lake
def test_neutral_environment_leaves_rates_untouched(calibrated):
    _, _, env = calibrated
    assert env.outcome_multiplier(1.0) == pytest.approx(np.ones(len(OUTCOMES)), abs=1e-3)


@needs_lake
def test_multiplier_clips_rather_than_extrapolates(calibrated):
    """Outside the calibrated grid the exponent solve is unverified."""
    _, _, env = calibrated
    assert env.outcome_multiplier(5.0) == pytest.approx(env.outcome_multiplier(MAX_MULTIPLIER))
    assert env.outcome_multiplier(0.1) == pytest.approx(env.outcome_multiplier(MIN_MULTIPLIER))


@needs_lake
def test_multiplier_is_monotonic_in_run_environment(calibrated):
    _, _, env = calibrated
    hr = OUTCOMES.index("HR")
    values = [env.outcome_multiplier(m)[hr] for m in (0.85, 0.95, 1.05, 1.15, 1.25)]
    assert values == sorted(values)


# --- game environments ----------------------------------------------------
@needs_lake
def test_every_game_gets_an_environment_and_none_are_absurd():
    envs = load_game_environments(2025)
    assert len(envs) > 2000
    values = np.array(list(envs.values()))
    assert values.min() > 0.6 and values.max() < 1.8
    # A missing join would silently make every game exactly neutral.
    assert values.std() > 0.02, "environments have no spread — check the weather join"


# --- engine integration ---------------------------------------------------
@needs_lake
def test_engine_scoring_scales_with_the_environment(calibrated):
    from src.simulation.game_engine import GameSimulator, TeamSetup
    from src.simulation.lineup import Bullpen, Lineup, SubstitutionPolicy

    model, tm, env = calibrated
    sim = GameSimulator(model, tm, SubstitutionPolicy.load("2023-2026"), run_environment=env)
    setup = TeamSetup(
        Lineup(tuple(range(900001, 900010))),
        910000,
        Bullpen(1, np.arange(910001, 910006), np.ones(5)),
    )
    rng = np.random.default_rng(5)
    low = sim.simulate(setup, setup, n_sims=6000, rng=rng, run_environment=0.85)
    high = sim.simulate(setup, setup, n_sims=6000, rng=rng, run_environment=1.25)
    assert high.mean_total_runs() > low.mean_total_runs() * 1.25


@needs_lake
def test_engine_without_an_environment_is_unaffected_by_the_parameter(calibrated):
    """Backwards compatibility: run_environment must be inert when none is configured."""
    from src.simulation.game_engine import GameSimulator, TeamSetup
    from src.simulation.lineup import Bullpen, Lineup, SubstitutionPolicy

    model, tm, _ = calibrated
    sim = GameSimulator(model, tm, SubstitutionPolicy.load("2023-2026"), run_environment=None)
    setup = TeamSetup(
        Lineup(tuple(range(900001, 900010))),
        910000,
        Bullpen(1, np.arange(910001, 910006), np.ones(5)),
    )
    a = sim.simulate(setup, setup, n_sims=1500, rng=np.random.default_rng(1), run_environment=0.8)
    b = sim.simulate(setup, setup, n_sims=1500, rng=np.random.default_rng(1), run_environment=1.4)
    assert (a.home_score == b.home_score).all()
