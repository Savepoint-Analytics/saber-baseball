"""Lineups, substitution policy, and bullpen selection.

The failure modes guarded here are all silent: a lineup that does not cycle, a
reliever who can pitch twice in one game, a hazard lookup whose buckets have drifted
away from the table it indexes. Each one produces a simulation that runs to
completion and reports the wrong answer.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.config import settings
from src.simulation.lineup import (
    BF_BUCKETS,
    LINEUP_SIZE,
    Bullpen,
    Lineup,
    SubstitutionPolicy,
    bf_bucket,
)

LAKE = (settings.data_dir / "saber.duckdb").resolve()
needs_lake = pytest.mark.skipif(not LAKE.exists(), reason="DuckDB lake not built")


def _lineup() -> Lineup:
    return Lineup(tuple(range(1, LINEUP_SIZE + 1)))


# --- lineup ---------------------------------------------------------------
def test_lineup_must_be_nine_batters():
    with pytest.raises(ValueError, match="9 batters"):
        Lineup((1, 2, 3))


def test_batting_order_cycles():
    """Slot is cyclic by rule; this is why it never has to be inferred."""
    lu = _lineup()
    assert lu.batter_for(0) == lu.batters[0]
    assert lu.batter_for(8) == lu.batters[8]
    assert lu.batter_for(9) == lu.batters[0]
    assert lu.batter_for(17) == lu.batters[8]


def test_every_slot_is_used_once_per_turn_through():
    lu = _lineup()
    assert sorted(lu.batter_for(i) for i in range(9)) == sorted(lu.batters)


# --- hazard bucketing -----------------------------------------------------
def test_bf_buckets_match_the_table_they_index():
    """A drift between these edges and gd_pitcher_removal is a silent lookup miss."""
    assert bf_bucket(1) == 6
    assert bf_bucket(6) == 6
    assert bf_bucket(7) == 12
    assert bf_bucket(30) == 30
    assert bf_bucket(31) == BF_BUCKETS[-1]
    assert bf_bucket(500) == BF_BUCKETS[-1]


def test_unknown_hazard_cells_fall_back_rather_than_raising():
    pol = SubstitutionPolicy(era="test", hazard={}, fallback=0.2)
    assert pol.removal_probability(True, 1, 5) == pytest.approx(0.2)


def test_should_remove_respects_the_hazard():
    """A hazard of 0 never removes; a hazard of 1 always does."""
    rng = np.random.default_rng(0)
    never = SubstitutionPolicy(era="t", hazard={(True, 1, 6): 0.0})
    always = SubstitutionPolicy(era="t", hazard={(True, 1, 6): 1.0})
    assert not any(never.should_remove(rng, True, 1, 3) for _ in range(200))
    assert all(always.should_remove(rng, True, 1, 3) for _ in range(200))


# --- bullpen --------------------------------------------------------------
def test_bullpen_weights_are_normalised():
    bp = Bullpen(1, np.array([10, 11, 12]), np.array([1.0, 2.0, 7.0]))
    assert bp.weights.sum() == pytest.approx(1.0)
    assert bp.weights[2] == pytest.approx(0.7)


def test_bullpen_rejects_zero_usage():
    with pytest.raises(ValueError, match="no usage weight"):
        Bullpen(1, np.array([10]), np.array([0.0]))


def test_bullpen_excludes_pitchers_already_used():
    """Without this a team reuses its best arm and every bullpen looks deeper."""
    rng = np.random.default_rng(3)
    bp = Bullpen(1, np.array([10, 11, 12]), np.array([1.0, 1.0, 1.0]))
    picks = {bp.draw(rng, exclude={10, 11}) for _ in range(50)}
    assert picks == {12}


def test_exhausted_bullpen_allows_reuse_rather_than_failing():
    """A game must finish; running out of arms cannot raise mid-simulation."""
    rng = np.random.default_rng(3)
    bp = Bullpen(1, np.array([10, 11]), np.array([1.0, 1.0]))
    assert bp.draw(rng, exclude={10, 11}) in (10, 11)


# --- against the real tables ---------------------------------------------
@needs_lake
def test_starter_hazard_rises_with_times_through_the_order():
    """The third-time-through effect must be present, not assumed."""
    pol = SubstitutionPolicy.load("2023-2026")
    first = pol.removal_probability(True, 1, 6)
    third = pol.removal_probability(True, 3, 24)
    assert third > first * 5, f"TTO hook missing: {first:.4f} -> {third:.4f}"


@needs_lake
def test_sampling_the_policy_reproduces_real_starter_workload():
    """End-to-end check: drawing PA by PA must yield realistic outings."""
    rng = np.random.default_rng(11)
    pol = SubstitutionPolicy.load("2023-2026")
    lengths = []
    for _ in range(4000):
        bf = 0
        while True:
            bf += 1
            if pol.should_remove(rng, True, (bf - 1) // 9 + 1, bf) or bf >= 45:
                break
        lengths.append(bf)
    mean = float(np.mean(lengths))
    assert 19.5 < mean < 24.0, f"simulated starters face {mean:.1f} batters"


# --- leverage-aware bullpen ------------------------------------------------
def test_leverage_band_boundaries():
    from src.simulation.lineup import leverage_band

    assert leverage_band(0) == "close"
    assert leverage_band(2) == "close"
    assert leverage_band(3) == "medium"
    assert leverage_band(5) == "medium"
    assert leverage_band(6) == "blowout"


def test_bullpen_without_quality_ignores_margin():
    """Backwards compatibility: a staff with no quality data draws by usage alone."""
    rng = np.random.default_rng(0)
    bp = Bullpen(1, np.array([10, 11]), np.array([1.0, 1.0]))
    assert bp.tilt_for(0) == 0.0
    assert bp.draw(rng, margin=9) in (10, 11)


def test_tilt_favours_better_relievers_in_close_games():
    """Lower on-base allowed is better, so a close-game target must prefer it."""
    quality = np.array([0.28, 0.31, 0.36])  # good, average, poor
    bp = Bullpen(
        1,
        np.array([10, 11, 12]),
        np.ones(3),
        quality=quality,
        targets={"close": 0.29, "medium": 0.315, "blowout": 0.34},
    )
    assert bp.tilt_for(1) > bp.tilt_for(8), "close games should tilt harder toward quality"

    rng = np.random.default_rng(1)
    close = [bp.draw(rng, margin=1) for _ in range(3000)]
    blowout = [bp.draw(rng, margin=9) for _ in range(3000)]
    q = dict(zip([10, 11, 12], quality, strict=True))
    assert np.mean([q[p] for p in close]) < np.mean([q[p] for p in blowout])


def test_tilt_gives_up_rather_than_distorting_an_impossible_target():
    """A target outside the staff's range must not collapse it onto one arm."""
    bp = Bullpen(
        1,
        np.array([10, 11]),
        np.ones(2),
        quality=np.array([0.30, 0.32]),
        targets={"close": 0.10},  # unreachable
    )
    assert bp.tilt_for(1) == 0.0
