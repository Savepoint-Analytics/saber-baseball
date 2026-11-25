"""Win probability and leverage tables, solved analytically from the engine (Phase 2).

``P(home wins | inning, half, score difference, base-out state)`` for every
situation a plate appearance can start or end in. This is the table WPA is read
from -- a batter's WPA is the change in his team's win probability across his
plate appearance -- and leverage index is the expected size of that change.

**Not simulated: solved.** The transition matrix gives, for any base-out state, the
exact distribution of runs scored over the rest of the half-inning (a linear system
in the run count; see ``_run_distribution``). Backward induction over half-innings
then gives win probability from every situation, with the ninth-inning and
extra-innings rules applied explicitly: the home side does not bat when ahead after
the top of the ninth, a walk-off ends play the instant the home side leads, and tied
extra innings are a fixed point because every extra inning looks the same. Monte
Carlo would need millions of games per season to get the tails of this table right
and would still be noisy; the solve is exact and takes under a second.

**Same components as RE24.** The outcome rates are the odds-ratio model's league,
base-out-state and home/away terms (``PAOutcomeModel``) and the transitions are
``gd_base_transitions`` -- the pieces validated for the simulator in
``docs/methodology.md``. So RE24 and WPA disagree only where context matters,
never because they were built from different views of the game.

**Per season**, like ``gd_run_expectancy_season``: the run environment moves, the
ghost-runner rule started in 2020, and a table per season keeps "above average"
season-relative. Innings 1-9 are indexed as themselves; index 10 stands for *any*
extra inning, since extras are stationary. Seven-inning games (2020-21
doubleheaders) are handled by the caller shifting the inning by two.

**Leverage index** is ``E|dWP|`` over the next plate appearance -- every outcome and
every base-runner transition it can produce -- divided by the season's mean of that
quantity over its real plate appearances, so the average PA has leverage 1.0.
Tango's definition, computed from our own table.

The martingale property is the built-in check: averaged over what the next PA can
do, win probability must not change. ``martingale_error`` reports the largest
violation; a test holds it at floating-point size.
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb
import numpy as np
import pandas as pd

from src.simulation.game_engine import AWAY, GHOST_RUNNER_STATE, HOME, REGULATION_INNINGS
from src.simulation.pa_model import PAOutcomeModel
from src.simulation.transitions import (
    MAX_RUNS,
    N_LIVE_STATES,
    N_STATES,
    TERMINAL_STATE,
    TransitionMatrix,
    _lake,
)

EXTRA_INNING = REGULATION_INNINGS + 1  # index for "any extra inning"
N_INNINGS = EXTRA_INNING + 1  # array axis 0 is 1..10; index 0 is unused
MAX_DIFF = 20  # score differences beyond this are clamped; WP is ~0 or ~1 there anyway
N_DIFF = 2 * MAX_DIFF + 1
RUN_CAP = 30  # runs in the rest of a half-inning; mass beyond this is ~1e-12
GHOST_RUNNER_FROM = 2020


def diff_index(diff: np.ndarray | int) -> np.ndarray:
    """Score difference (home - away) -> array index, clamped to +-MAX_DIFF."""
    return np.clip(np.asarray(diff, dtype=int), -MAX_DIFF, MAX_DIFF) + MAX_DIFF


def effective_inning(
    inning: np.ndarray | int, regulation_innings: np.ndarray | int = 9
) -> np.ndarray:
    """Inning as the table sees it: shifted for seven-inning games, capped at extras."""
    shifted = np.asarray(inning, dtype=int) + (REGULATION_INNINGS - np.asarray(regulation_innings))
    return np.clip(shifted, 1, EXTRA_INNING)


def _run_distribution(joint: np.ndarray) -> np.ndarray:
    """``P(r more runs this half-inning | live state)``, shape ``(24, RUN_CAP + 1)``.

    ``joint[s, s', r]`` is ``P(next state s', r runs on the play | state s)``. Runs
    accumulate, so condition on the runs scored on the *first* play and recurse:

        P(r | s) = sum_{s' live, r' <= r} joint[s, s', r'] P(r - r' | s') + joint[s, end, r]

    The ``r' = 0`` term references ``P(r | .)`` itself, so each run count is a
    24x24 linear solve given the smaller counts -- exact, no convergence loop.
    """
    live = joint[:, :N_LIVE_STATES, :]  # (24, 24, runs)
    end = joint[:, TERMINAL_STATE, :]  # (24, runs)
    solve = np.linalg.inv(np.eye(N_LIVE_STATES) - live[:, :, 0])
    out = np.zeros((N_LIVE_STATES, RUN_CAP + 1))
    for r in range(RUN_CAP + 1):
        b = end[:, r] if r <= MAX_RUNS else np.zeros(N_LIVE_STATES)
        for rp in range(1, min(r, MAX_RUNS) + 1):
            b = b + live[:, :, rp] @ out[:, r - rp]
        out[:, r] = solve @ b
    # Lump the negligible tail into the cap so every row is a distribution.
    out[:, RUN_CAP] += 1.0 - out.sum(axis=1)
    return out


@dataclass
class WinProbabilityTable:
    """Home win probability and leverage for every (inning, half, diff, state)."""

    season: int
    # (N_INNINGS, 2, N_DIFF, N_STATES): state 24 means "this half-inning is over".
    wp: np.ndarray
    # (N_INNINGS, 2, N_DIFF, N_LIVE_STATES): expected |dWP| over the next PA.
    dwp: np.ndarray
    mean_dwp: float
    ghost_runner: bool

    @property
    def leverage(self) -> np.ndarray:
        return self.dwp / self.mean_dwp

    def lookup(
        self, inning: np.ndarray, half: np.ndarray, diff: np.ndarray, state: np.ndarray
    ) -> np.ndarray:
        """Vectorised win probability; ``half`` is 0 for the top (away batting)."""
        return self.wp[
            effective_inning(inning),
            np.asarray(half, dtype=int),
            diff_index(diff),
            np.asarray(state, dtype=int),
        ]

    def martingale_error(self, joint: tuple[np.ndarray, np.ndarray]) -> float:
        """Largest |E[WP after one PA] - WP before| over every live situation."""
        worst = 0.0
        for inning in range(1, N_INNINGS):
            for half in (AWAY, HOME):
                after = self._after_values(inning, half, joint[half])
                expected = np.einsum("snr,dsnr->ds", joint[half], after)
                before = self.wp[inning, half, :, :N_LIVE_STATES]
                worst = max(worst, float(np.abs(expected - before).max()))
        return worst

    def _after_values(self, inning: int, half: int, joint: np.ndarray) -> np.ndarray:
        """WP after each (next state, runs) from every (diff, state): ``(N_DIFF, 24, 25, R)``.

        Independent of the current state -- only the new difference and the new
        state matter -- so it is computed once per difference and broadcast.
        """
        sign = 1 if half == HOME else -1
        d = np.arange(-MAX_DIFF, MAX_DIFF + 1)
        r = np.arange(joint.shape[2])
        new_idx = diff_index(d[:, None] + sign * r[None, :])  # (N_DIFF, R)
        after = self.wp[inning, half][new_idx]  # (N_DIFF, R, N_STATES)
        after = after.transpose(0, 2, 1)  # (N_DIFF, N_STATES, R)
        return np.broadcast_to(after[:, None, :, :], (N_DIFF, N_LIVE_STATES, N_STATES, len(r)))

    def to_frame(self) -> pd.DataFrame:
        """Long form for the warehouse: one row per (inning, half, diff, state)."""
        innings, halves, diffs, states = np.meshgrid(
            np.arange(1, N_INNINGS),
            np.arange(2),
            np.arange(-MAX_DIFF, MAX_DIFF + 1),
            np.arange(N_STATES),
            indexing="ij",
        )
        li = np.full(self.wp.shape, np.nan)
        li[..., :N_LIVE_STATES] = self.leverage
        frame = pd.DataFrame(
            {
                "season": self.season,
                "inning": innings.ravel(),
                "half": np.where(halves.ravel() == HOME, "Bot", "Top"),
                "score_diff": diffs.ravel(),
                "state": states.ravel(),
                "win_prob": self.wp[1:].ravel(),
                "leverage": li[1:].ravel(),
            }
        )
        return frame


def situation_kernels(
    pa_model: PAOutcomeModel, transitions: TransitionMatrix
) -> tuple[np.ndarray, np.ndarray]:
    """``P(next state, runs | state)`` for the away and home sides batting.

    League-average batter against a league-average pitcher, with the base-out
    state term and the home/away term -- the same odds-ratio composition the engine
    uses, minus the two player terms.
    """
    lg = np.maximum(pa_model.league_probabilities(), 1e-12)
    state_rates = pa_model.state_probabilities()
    side_rates = pa_model.side_probabilities()
    kernels = []
    for side in (AWAY, HOME):
        raw = state_rates * side_rates[side] / lg
        per_state = raw / raw.sum(axis=1, keepdims=True)
        kernels.append(np.einsum("so,sonr->snr", per_state, transitions.probs))
    return kernels[AWAY], kernels[HOME]


def build_table(
    season: int,
    pa_model: PAOutcomeModel,
    transitions: TransitionMatrix,
    situations: pd.DataFrame | None = None,
) -> WinProbabilityTable:
    """Solve the table for one season.

    ``situations`` (columns ``inning``, ``half``, ``score_diff``, ``state``, ``n``)
    is the season's real plate-appearance mix, used only to scale leverage so the
    average PA is 1.0. Without it, leverage is scaled to the bases-empty,
    none-out, tied first-inning PA instead.
    """
    ghost = season >= GHOST_RUNNER_FROM
    joint = situation_kernels(pa_model, transitions)
    runs = (_run_distribution(joint[AWAY]), _run_distribution(joint[HOME]))
    r = np.arange(RUN_CAP + 1)

    wp = np.full((N_INNINGS, 2, N_DIFF, N_STATES), np.nan)
    diffs = np.arange(-MAX_DIFF, MAX_DIFF + 1)
    start_extra = GHOST_RUNNER_STATE if ghost else 0

    def home_needs(runs_home: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """From the bottom of the ninth or later: P(home wins this half), P(it ends tied).

        The home side needs ``1 - d`` runs to lead, and play stops the moment it
        does, so the untruncated run distribution's upper tail is exactly the win
        probability. Each is ``(N_DIFF, 24)``.
        """
        cum = np.cumsum(runs_home[:, ::-1], axis=1)[:, ::-1]  # cum[s, k] = P(r >= k | s)
        need = np.clip(1 - diffs, 0, RUN_CAP)
        win = np.where(diffs[:, None] > 0, 1.0, cum[:, need].T)
        tie = np.where(diffs[:, None] <= 0, runs_home[:, np.clip(-diffs, 0, RUN_CAP)].T, 0.0)
        return win, tie

    # Extra innings: tied top-of-inning value X is a fixed point. From the top,
    # the away side scores a; the home side then needs a+1 to win, exactly a to
    # tie and return to X.
    away_runs = runs[AWAY][start_extra]  # (R,)
    home_cum = np.cumsum(runs[HOME][start_extra][::-1])[::-1]
    a_win = sum(away_runs[a] * (home_cum[a + 1] if a + 1 <= RUN_CAP else 0.0) for a in r)
    a_tie = sum(away_runs[a] * runs[HOME][start_extra][a] for a in r)
    x_tied = a_win / (1.0 - a_tie)

    def fill_late_bottom(inning: int, tie_value: float) -> None:
        win, tie = home_needs(runs[HOME])
        late = win + tie * tie_value
        wp[inning, HOME, :, :N_LIVE_STATES] = np.where(diffs[:, None] > 0, 1.0, late)
        wp[inning, HOME, :, TERMINAL_STATE] = np.select(
            [diffs > 0, diffs == 0], [1.0, tie_value], 0.0
        )

    def fill_top(inning: int, start_bottom: int) -> None:
        # Value once the top half is over: no bottom half needed from the ninth on
        # when the home side leads; otherwise the bottom half from its start state.
        over = wp[inning, HOME, :, start_bottom].copy()
        if inning >= REGULATION_INNINGS:
            over = np.where(diffs > 0, 1.0, over)
        wp[inning, AWAY, :, TERMINAL_STATE] = over
        after_idx = diff_index(diffs[:, None] - r[None, :])  # (N_DIFF, R)
        wp[inning, AWAY, :, :N_LIVE_STATES] = np.einsum("sr,dr->ds", runs[AWAY], over[after_idx])

    fill_late_bottom(EXTRA_INNING, x_tied)
    fill_top(EXTRA_INNING, start_extra)
    fill_late_bottom(REGULATION_INNINGS, x_tied)
    fill_top(REGULATION_INNINGS, 0)
    for inning in range(REGULATION_INNINGS - 1, 0, -1):
        over_bottom = wp[inning + 1, AWAY, :, 0]
        wp[inning, HOME, :, TERMINAL_STATE] = over_bottom
        after_idx = diff_index(diffs[:, None] + r[None, :])
        wp[inning, HOME, :, :N_LIVE_STATES] = np.einsum(
            "sr,dr->ds", runs[HOME], over_bottom[after_idx]
        )
        fill_top(inning, 0)

    table = WinProbabilityTable(
        season=season, wp=wp, dwp=np.zeros(1), mean_dwp=1.0, ghost_runner=ghost
    )

    dwp = np.zeros((N_INNINGS, 2, N_DIFF, N_LIVE_STATES))
    for inning in range(1, N_INNINGS):
        for half in (AWAY, HOME):
            after = table._after_values(inning, half, joint[half])
            before = wp[inning, half, :, :N_LIVE_STATES]
            dwp[inning, half] = np.einsum(
                "snr,dsnr->ds", joint[half], np.abs(after - before[:, :, None, None])
            )
    table.dwp = dwp

    if situations is not None and len(situations):
        idx = (
            effective_inning(situations["inning"].to_numpy()),
            situations["half"].to_numpy(dtype=int),
            diff_index(situations["score_diff"].to_numpy()),
            situations["state"].to_numpy(dtype=int),
        )
        weights = situations["n"].to_numpy(dtype=float)
        table.mean_dwp = float(np.average(dwp[idx], weights=weights))
    else:
        table.mean_dwp = float(dwp[1, AWAY, MAX_DIFF, 0])
    return table


# --- lake access ------------------------------------------------------------


def load_situations(season: int, con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """How often each (inning, half, diff, state) actually occurred in a season."""
    return con.sql(
        f"""
        select inning + 9 - regulation_innings                    as inning,
               case when inning_topbot = 'Bot' then 1 else 0 end  as half,
               home_score_before - away_score_before              as score_diff,
               state_before                                       as state,
               count(*)                                           as n
        from main_silver.sv_plate_appearances
        where season = {int(season)} and season_phase = 'regular'
        group by 1, 2, 3, 4
        """
    ).df()


def build_for_season(
    season: int, con: duckdb.DuckDBPyConnection | None = None
) -> WinProbabilityTable:
    """Fit the inputs from the lake and solve the table for one season."""
    own = con is None
    con = con or _lake()
    try:
        pa = con.sql(
            f"""
            select batter, pitcher, outcome, state_before,
                   inning_topbot = 'Bot' as is_home_batting
            from main_silver.sv_plate_appearances
            where season = {int(season)} and season_phase = 'regular'
            """
        ).df()
        transitions = TransitionMatrix.load(_era(season), con=con)
        situations = load_situations(season, con)
    finally:
        if own:
            con.close()
    if pa.empty:
        raise ValueError(f"no regular-season plate appearances for {season}")
    return build_table(season, PAOutcomeModel().fit(pa), transitions, situations)


def _era(season: int) -> str:
    if season <= 2018:
        return "2015-2018"
    if season <= 2022:
        return "2019-2022"
    return "2023-2026"
