"""Run expectancy (RE24) and Win Probability Added (WPA) — Phase 2.

The RE24 matrix and the per-plate-appearance credit are built in dbt
(``gd_run_expectancy_season`` → ``gd_pa_run_value`` → ``gd_player_re24``), because
that is where every other transform lives and where the tests run. This module is
the **pandas mirror** of those two steps: the same arithmetic on a frame, so a test
can rebuild the matrix from the lake and assert the SQL and the Python agree to
floating-point precision. It is also what a notebook reaches for to value a handful
of plate appearances without a dbt run.

    re24 = RE(state_after) - RE(state_before) + runs_on_play

with RE(after) = 0 once the third out is recorded.

WPA is the same construction on win probability. The table is solved in
``src.features.win_probability`` (a dbt Python model materialises it as
``gd_win_probability``) and ``wpa_credit`` below is the pandas mirror of the join
in ``gd_pa_run_value``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from src.simulation.transitions import _lake

if TYPE_CHECKING:
    from src.features.win_probability import WinProbabilityTable

N_STATES = 24


def build_re24(pa: pd.DataFrame) -> pd.Series:
    """RE24 matrix from a plate-appearance frame: expected runs to end of inning by state.

    ``pa`` needs ``state_before``, ``runs_to_end_of_inning`` and
    ``in_complete_half_inning`` — the columns ``sv_plate_appearances`` carries. Only
    complete half-innings are averaged, for the reason documented on that model: a
    walk-off stops play mid-inning, so the game's last half-inning is truncated by
    rule and would drag every state's expectancy down. Returns a Series indexed by
    state 0-23.
    """
    missing = {"state_before", "runs_to_end_of_inning", "in_complete_half_inning"} - set(pa.columns)
    if missing:
        raise ValueError(f"plate appearances frame missing columns: {sorted(missing)}")
    complete = pa[pa["in_complete_half_inning"].astype(bool)]
    re = complete.groupby("state_before")["runs_to_end_of_inning"].mean()
    return re.reindex(range(N_STATES)).rename("run_expectancy")


def re24_credit(pa: pd.DataFrame, re: pd.Series) -> pd.Series:
    """Per-PA RE24 from the batting team's point of view, given a 24-state matrix.

    ``pa`` needs ``state_before``, ``outs_after``, ``b1_after``/``b2_after``/
    ``b3_after`` and ``runs_on_play``. Three outs after the PA — a real third out or
    a walk-off — zero the after-state expectancy; see ``gd_pa_run_value`` for why
    walk-offs are left that way.
    """
    state_after = (
        pa["outs_after"].astype(int) * 8
        + pa["b1_after"].astype(int)
        + pa["b2_after"].astype(int) * 2
        + pa["b3_after"].astype(int) * 4
    )
    re_before = pa["state_before"].map(re).astype(float)
    re_after = state_after.map(re).astype(float).where(pa["outs_after"] < 3, 0.0)
    return (re_after - re_before + pa["runs_on_play"]).rename("re24")


def load_run_expectancy(season: int) -> pd.Series:
    """The dbt-built per-season matrix, as a Series indexed by state."""
    con = _lake()
    try:
        frame = con.sql(
            f"""
            select state, run_expectancy
            from main_gold.gd_run_expectancy_season
            where season = {int(season)}
            order by state
            """
        ).df()
    finally:
        con.close()
    return frame.set_index("state")["run_expectancy"].reindex(range(N_STATES))


def build_wpa_table(season: int) -> pd.DataFrame:
    """The solved win-probability table for a season, long form (see ``gd_win_probability``)."""
    from src.features.win_probability import build_for_season

    return build_for_season(season).to_frame()


def wpa_credit(pa: pd.DataFrame, table: WinProbabilityTable) -> pd.Series:
    """Per-PA WPA for the batting team from a ``WinProbabilityTable``.

    ``pa`` needs ``inning``, ``regulation_innings``, ``inning_topbot``,
    ``home_score_before``, ``away_score_before``, ``state_before`` and, for the
    situation after, ``next_inning``, ``next_half``, ``next_home_score``,
    ``next_away_score``, ``next_state`` (null for a game's final PA) plus
    ``runs_on_play``. Mirrors ``gd_pa_run_value``: the after-situation is the next
    PA as observed, and only the game's last PA is scored against "half over".
    """
    from src.features.win_probability import TERMINAL_STATE as HALF_OVER
    from src.features.win_probability import effective_inning

    home = (pa["inning_topbot"] == "Bot").to_numpy()
    half = home.astype(int)
    diff_before = (pa["home_score_before"] - pa["away_score_before"]).to_numpy()
    before = table.lookup(
        effective_inning(pa["inning"].to_numpy(), pa["regulation_innings"].to_numpy()),
        half,
        diff_before,
        pa["state_before"].to_numpy(),
    )
    ended = pa["next_inning"].isna().to_numpy()
    inning_after = np.where(ended, pa["inning"], pa["next_inning"].fillna(0)).astype(int)
    half_after = np.where(ended, half, (pa["next_half"] == "Bot").astype(int))
    signed_runs = np.where(home, 1, -1) * pa["runs_on_play"].to_numpy()
    diff_after = np.where(
        ended,
        diff_before + signed_runs,
        (pa["next_home_score"].fillna(0) - pa["next_away_score"].fillna(0)).to_numpy(),
    ).astype(int)
    state_after = np.where(ended, HALF_OVER, pa["next_state"].fillna(0)).astype(int)
    after = table.lookup(
        effective_inning(inning_after, pa["regulation_innings"].to_numpy()),
        half_after,
        diff_after,
        state_after,
    )
    return pd.Series((after - before) * np.where(home, 1.0, -1.0), index=pa.index, name="wpa")
