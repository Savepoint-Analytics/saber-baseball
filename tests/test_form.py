"""Day-to-day pitcher form.

Two properties matter more than anything else here. The multiplier must be drawn once
per outing rather than per plate appearance — otherwise it adds variance without
adding correlation, which is the opposite of its purpose. And it must not change mean
run scoring, because runs are convex in pitcher quality and an uncorrected even mix of
good and bad nights quietly inflates offense.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.config import settings
from src.simulation.form import ON_BASE, PitcherForm, estimate_form_sd, load_direction
from src.simulation.pa_model import OUTCOMES, PAOutcomeModel
from src.simulation.transitions import TransitionMatrix

LAKE = (settings.data_dir / "saber.duckdb").resolve()
needs_lake = pytest.mark.skipif(not LAKE.exists(), reason="DuckDB lake not built")


@pytest.fixture(scope="module")
def calibrated():
    from src.simulation.transitions import load_plate_appearances

    model = PAOutcomeModel().fit(load_plate_appearances((2023, 2024, 2025, 2026)))
    tm = TransitionMatrix.load("2023-2026")
    return model, tm, PitcherForm.calibrate(model, tm)


def test_on_base_is_the_complement_of_strikeouts_and_outs():
    assert set(ON_BASE) == set(OUTCOMES) - {"K", "OUT"}


# --- direction ------------------------------------------------------------
@needs_lake
def test_direction_is_centred():
    d = load_direction("2023-2026")
    assert d.shape == (len(OUTCOMES),)
    assert d.mean() == pytest.approx(0.0, abs=1e-12)


@needs_lake
def test_a_bad_night_means_fewer_strikeouts_and_more_damage():
    d = dict(zip(OUTCOMES, load_direction("2023-2026"), strict=True))
    assert d["K"] < 0, "a pitcher who is off should miss fewer bats"
    assert d["HR"] > 0 and d["2B"] > 0


@needs_lake
def test_direction_is_not_dominated_by_the_split_variable():
    """Guards against regressing to a split that selects on the outcomes measured.

    Splitting on K-BB made walks appear to more than double; splitting on velocity is
    independent of every outcome, and no single component should tower over the rest.
    """
    d = load_direction("2023-2026")
    assert np.abs(d).max() < 0.5, "one outcome dominates — check the split variable"


# --- magnitude ------------------------------------------------------------
@needs_lake
def test_measured_form_sd_is_small_and_positive():
    """It is a real effect and a modest one; both halves matter."""
    sd = estimate_form_sd((2023, 2024, 2025, 2026))
    assert 0.005 < sd < 0.03, f"form sd {sd:.4f} outside a plausible range"


@needs_lake
def test_calibration_reproduces_the_measured_spread(calibrated):
    """Sigma exists to make the induced on-base spread match what was measured."""
    model, _, form = calibrated
    league = model.league_probabilities()
    mask = np.array([o in ON_BASE for o in OUTCOMES])
    nodes, weights = np.polynomial.hermite_e.hermegauss(15)
    weights = weights / weights.sum()
    values = []
    for n in nodes:
        rates = league * np.exp(form.sigma * n * form.direction)
        values.append((rates / rates.sum())[mask].sum())
    values = np.array(values)
    mean = np.dot(weights, values)
    induced = np.sqrt(np.dot(weights, (values - mean) ** 2))
    assert induced == pytest.approx(form.target_sd, rel=0.05)


@needs_lake
def test_mean_preservation_offset_is_negative(calibrated):
    """Convexity inflates runs, so the correction must push the other way.

    A positive offset would mean the calibration had the sign backwards and was
    adding offense on top of the inflation it exists to remove.
    """
    _, _, form = calibrated
    assert form.offset < 0


@needs_lake
def test_adding_form_does_not_change_expected_run_scoring(calibrated):
    """The property the offset buys: variance without a scoring shift."""
    model, tm, form = calibrated
    base = model.state_probabilities()
    baseline = tm.run_expectancy(base)[0]
    nodes, weights = np.polynomial.hermite_e.hermegauss(15)
    weights = weights / weights.sum()
    total = 0.0
    for n, w in zip(nodes, weights, strict=True):
        adj = base * np.exp((form.sigma * n + form.offset) * form.direction)
        adj = adj / adj.sum(axis=1, keepdims=True)
        total += w * tm.run_expectancy(adj)[0]
    assert total == pytest.approx(baseline, rel=1e-3)


# --- draws ----------------------------------------------------------------
@needs_lake
def test_draw_returns_one_multiplier_per_outing(calibrated):
    _, _, form = calibrated
    m = form.draw(np.random.default_rng(0), 500)
    assert m.shape == (500, len(OUTCOMES))
    assert (m > 0).all()


@needs_lake
def test_draws_actually_vary(calibrated):
    """A silently zero sigma would make the whole feature a no-op."""
    _, _, form = calibrated
    m = form.draw(np.random.default_rng(0), 2000)
    assert m.std(axis=0).max() > 0.01


def test_zero_sigma_yields_neutral_multipliers():
    """When the data shows no form effect, the term must vanish, not fabricate one."""
    form = PitcherForm(direction=np.ones(len(OUTCOMES)), sigma=0.0, offset=0.0, target_sd=0.0)
    m = form.draw(np.random.default_rng(0), 50)
    assert m == pytest.approx(np.ones((50, len(OUTCOMES))))


@needs_lake
def test_engine_without_form_is_unaffected(calibrated):
    """Backwards compatibility: the engine must run identically with form absent."""
    from src.simulation.game_engine import GameSimulator, TeamSetup
    from src.simulation.lineup import Bullpen, Lineup, SubstitutionPolicy

    model, tm, _ = calibrated
    setup = TeamSetup(
        Lineup(tuple(range(900001, 900010))),
        910000,
        Bullpen(1, np.arange(910001, 910006), np.ones(5)),
    )
    sim = GameSimulator(model, tm, SubstitutionPolicy.load("2023-2026"), pitcher_form=None)
    a = sim.simulate(setup, setup, n_sims=800, rng=np.random.default_rng(3))
    b = sim.simulate(setup, setup, n_sims=800, rng=np.random.default_rng(3))
    assert (a.home_score == b.home_score).all()
