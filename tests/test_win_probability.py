"""Win probability and leverage tables (Phase 2).

The table is solved, not sampled, so the properties here are exact ones: run
distributions that sum to one, a martingale that holds to floating point, a fixed
point that is actually fixed. The lake-backed checks then ask whether the numbers
are the *right* exact numbers -- calibrated against real outcomes and landing where
the published tables land.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.features.win_probability import (
    EXTRA_INNING,
    MAX_DIFF,
    N_LIVE_STATES,
    RUN_CAP,
    _run_distribution,
    diff_index,
    effective_inning,
)
from src.simulation.game_engine import AWAY, GHOST_RUNNER_STATE, HOME
from src.simulation.transitions import N_STATES, TERMINAL_STATE
from tests.conftest import LAKE, requires_lake

needs_lake = requires_lake(
    "main_gold.gd_win_probability",
    "main_gold.gd_pa_run_value.home_wp_before",
    "main_gold.gd_batter_season.wpa",
    "main_gold.gd_pitcher_season.mean_leverage",
    "main_silver.sv_plate_appearances.regulation_innings",
)


def test_diff_index_clamps_symmetrically():
    assert diff_index(0) == MAX_DIFF
    assert diff_index(-MAX_DIFF - 5) == 0
    assert diff_index(MAX_DIFF + 5) == 2 * MAX_DIFF
    assert list(diff_index(np.array([-1, 0, 1]))) == [MAX_DIFF - 1, MAX_DIFF, MAX_DIFF + 1]


def test_effective_inning_shifts_seven_inning_games_and_caps_extras():
    assert effective_inning(7, 7) == 9
    assert effective_inning(1, 7) == 3
    assert effective_inning(12, 9) == EXTRA_INNING
    assert list(effective_inning(np.array([1, 9, 10]), np.array([9, 9, 9]))) == [1, 9, EXTRA_INNING]


def _toy_kernel(p_out: float, p_score: float) -> np.ndarray:
    """From every state: an out with prob p_out, a run and stay put otherwise.

    Three outs is modelled crudely as 'out -> terminal', so the half-inning ends
    after one out. The run distribution is then geometric with success p_out.
    """
    joint = np.zeros((N_LIVE_STATES, N_STATES, 7))
    for s in range(N_LIVE_STATES):
        joint[s, TERMINAL_STATE, 0] = p_out
        joint[s, s, 1] = p_score
    return joint


def test_run_distribution_is_a_distribution_and_geometric_on_the_toy():
    dist = _run_distribution(_toy_kernel(0.7, 0.3))
    assert dist.shape == (N_LIVE_STATES, RUN_CAP + 1)
    np.testing.assert_allclose(dist.sum(axis=1), 1.0, atol=1e-12)
    # P(r runs) = 0.3^r * 0.7 for every state.
    for r in range(5):
        np.testing.assert_allclose(dist[:, r], 0.3**r * 0.7, atol=1e-12)


def test_run_distribution_certain_out_scores_nothing():
    dist = _run_distribution(_toy_kernel(1.0, 0.0))
    np.testing.assert_allclose(dist[:, 0], 1.0)
    assert dist[:, 1:].sum() == 0.0


# --- lake-backed -------------------------------------------------------------


@pytest.fixture(scope="module")
def table_2025():
    from src.features.win_probability import build_for_season

    return build_for_season(2025)


@pytest.fixture(scope="module")
def kernels_2025():
    import duckdb

    from src.features.win_probability import situation_kernels
    from src.simulation.pa_model import PAOutcomeModel
    from src.simulation.transitions import TransitionMatrix

    con = duckdb.connect(str(LAKE), read_only=True)
    try:
        pa = con.sql(
            "select batter, pitcher, outcome, state_before, inning_topbot = 'Bot' as is_home_batting "
            "from main_silver.sv_plate_appearances where season = 2025 and season_phase = 'regular'"
        ).df()
        tm = TransitionMatrix.load("2023-2026", con=con)
    finally:
        con.close()
    return situation_kernels(PAOutcomeModel().fit(pa), tm)


@needs_lake
def test_table_is_complete_and_bounded(table_2025):
    wp = table_2025.wp[1:]
    assert not np.isnan(wp).any()
    assert wp.min() >= 0.0 and wp.max() <= 1.0
    assert not np.isnan(table_2025.dwp[1:]).any()


@needs_lake
def test_win_probability_is_a_martingale(table_2025, kernels_2025):
    """Averaged over what the next PA can do, WP must not move. Exact by construction."""
    assert table_2025.martingale_error(kernels_2025) < 1e-12


@needs_lake
def test_extra_innings_fixed_point_is_fixed(table_2025):
    """The tied top-of-an-extra-inning value used inside the bottom-half solve must
    equal the value the table then reports for that situation."""
    wp = table_2025.wp
    x = wp[EXTRA_INNING, AWAY, MAX_DIFF, GHOST_RUNNER_STATE]
    # Bottom of an extra inning, tied, half over -> back to the top, tied.
    assert wp[EXTRA_INNING, HOME, MAX_DIFF, TERMINAL_STATE] == pytest.approx(x)
    assert wp[9, HOME, MAX_DIFF, TERMINAL_STATE] == pytest.approx(x)


@needs_lake
def test_rules_are_encoded(table_2025):
    wp = table_2025.wp
    # Home leads after the top of the ninth: no bottom half, game over.
    assert wp[9, AWAY, MAX_DIFF + 1, TERMINAL_STATE] == 1.0
    # Walk-off: home leads with the half still live in the ninth -> won.
    assert wp[9, HOME, MAX_DIFF + 2, 0] == 1.0
    # Three outs in the bottom of the ninth, still behind -> lost.
    assert wp[9, HOME, MAX_DIFF - 1, TERMINAL_STATE] == 0.0
    # Monotone in the score difference for a fixed situation.
    row = wp[5, AWAY, :, 0]
    assert np.all(np.diff(row) >= 0)
    # More outs, same bases, batting side behind -> worse for the batting side.
    assert (
        wp[7, HOME, MAX_DIFF - 1, 0] > wp[7, HOME, MAX_DIFF - 1, 8] > wp[7, HOME, MAX_DIFF - 1, 16]
    )


@needs_lake
def test_lands_on_the_published_values(table_2025):
    """Measured from our own components, and they agree with the literature."""
    wp, li = table_2025.wp, table_2025.leverage
    assert 0.50 < wp[1, AWAY, MAX_DIFF, 0] < 0.56  # home advantage at first pitch
    assert 0.60 < wp[9, HOME, MAX_DIFF, 0] < 0.68  # bottom 9, tied, nobody on
    assert 0.68 < wp[9, HOME, MAX_DIFF - 1, 7] < 0.78  # bottom 9, down 1, loaded, none out
    assert 0.030 < table_2025.mean_dwp < 0.040  # Tango: ~0.0346
    assert 0.8 < li[1, AWAY, MAX_DIFF, 0] < 0.95  # leadoff PA of the game
    assert 2.0 < li[9, HOME, MAX_DIFF, 7] < 3.0  # bottom 9, tied, loaded, none out
    assert li[8, AWAY, MAX_DIFF + 10, 0] < 0.05  # up ten in the eighth


@needs_lake
def test_stated_win_probability_is_calibrated_on_real_games():
    """Bin every PA by stated home WP; the observed home win rate should track it.

    A small systematic under-confidence is expected and accepted: the table is
    team-agnostic, and the side that is leading is on average the better side.
    """
    import duckdb

    con = duckdb.connect(str(LAKE), read_only=True)
    try:
        cal = con.sql(
            """
            select floor(v.home_wp_before * 10) as bin, count(*) as n,
                   avg(v.home_wp_before) as stated, avg(g.home_win) as observed
            from main_gold.gd_pa_run_value v
            join main_silver.sv_games g using (game_pk)
            where g.home_win is not null and v.season_phase = 'regular'
            group by 1
            """
        ).df()
    finally:
        con.close()
    gap = (cal.observed - cal.stated).abs()
    assert float((gap * cal.n).sum() / cal.n.sum()) < 0.02
    assert gap.max() < 0.04


@needs_lake
def test_wpa_leaders_are_who_they_should_be():
    import duckdb

    con = duckdb.connect(str(LAKE), read_only=True)
    try:
        top = con.sql(
            """
            select p.name_full, b.wpa, b.wpa_per_li, b.mean_leverage
            from main_gold.gd_batter_season b
            join main_silver.sv_players p using (player_id)
            where b.season = 2025 and b.season_phase = 'regular'
            order by b.wpa desc limit 5
            """
        ).df()
        closers = con.sql(
            """
            select avg(mean_leverage) as li
            from main_gold.gd_pitcher_season
            where season = 2025 and season_phase = 'regular' and starts = 0 and games >= 50
              and re24_saved > 10
            """
        ).df()
    finally:
        con.close()
    assert "Aaron Judge" in set(top.name_full)
    assert (top.wpa > 3.5).all()
    # Regular batters sit near average leverage; good high-usage relievers well above.
    assert (top.mean_leverage.between(0.8, 1.2)).all()
    assert float(closers.li.iloc[0]) > 1.4
