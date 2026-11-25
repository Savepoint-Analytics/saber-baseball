"""Batter-by-pitch-family matchups.

The most important thing asserted here is a *negative*: this term is not wired into
the simulator, because on held-out data it makes predictions significantly worse. The
tests pin the shrinkage that keeps its descriptive output honest, and pin the fact
that the engine does not consume it.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.simulation.pa_model import OUTCOMES, PAOutcomeModel
from src.simulation.pitch_matchup import FAMILIES, PitchMatchup, _regression_constant
from tests.conftest import requires_lake

needs_lake = requires_lake()


@pytest.fixture(scope="module")
def matchup():
    from src.simulation.transitions import load_plate_appearances

    model = PAOutcomeModel().fit(load_plate_appearances((2023, 2024, 2025)))
    return PitchMatchup.load(model, (2023, 2024, 2025))


def test_families_are_the_three_standard_groups():
    assert set(FAMILIES) == {"fastball", "breaking", "offspeed"}


def test_regression_constant_is_huge_when_spread_is_noise():
    """No detectable talent spread must regress essentially all the way to zero."""
    rel = np.zeros(50)
    n = np.full(50, 100.0)
    assert _regression_constant(rel, n, 0.32) >= 1e5


@needs_lake
def test_shrinkage_is_heavy_because_the_signal_is_weak(matchup):
    """Persistence is r ~ 0.15, so profiles must be pulled hard toward zero."""
    for fam in FAMILIES:
        assert matchup.shrink_k[fam] > 150, f"{fam} shrinkage too light for the signal"


@needs_lake
def test_adjustments_are_small(matchup):
    """A weak signal must not produce large matchup swings."""
    sample = list(matchup.profiles)[:500]
    pitcher = next(iter(matchup.mixes))
    adj = np.array([matchup.adjustment(b, pitcher) for b, _ in sample])
    assert np.abs(adj).max() < 0.06, "matchup adjustment is larger than the evidence supports"


@needs_lake
def test_unknown_batter_or_pitcher_yields_no_adjustment(matchup):
    assert matchup.adjustment(999999, 888888) == 0.0
    assert matchup.multiplier(np.array([999999]), np.array([888888])) == pytest.approx(
        np.ones((1, len(OUTCOMES)))
    )


def test_the_simulator_does_not_consume_this_term():
    """Pinned deliberately: held-out testing showed it degrades prediction (t = -2.77).

    If someone wires it into the engine, this test should fail and send them back to
    the module docstring to re-run the evaluation first.
    """
    import inspect

    from src.simulation import game_engine

    source = inspect.getsource(game_engine)
    assert "PitchMatchup" not in source
    assert "pitch_matchup" not in source
