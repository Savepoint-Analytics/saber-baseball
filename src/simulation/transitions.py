"""Base-out transition matrix and analytic run expectancy (Phases 4-5).

The second half of the simulation engine. ``gd_base_transitions`` holds the empirical
``P(next state, runs | state, outcome)``; this module loads it into dense arrays the
simulator can index, and — importantly — solves the resulting Markov chain in closed
form so the engine can be validated *before* any game loop exists.

**States** are 0-23 (``outs * 8 + base code``) plus 24, the absorbing three-out state.

**Why solve analytically.** Expected runs from each state can be obtained by
simulating innings, but then a bug in the sampler and a bug in the matrix are
indistinguishable. Solving ``RE = (I - Q)^-1 b`` uses only the matrix, so comparing
the result against the empirical RE24 in ``gd_run_expectancy`` tests the matrix on
its own. If those agree, any later disagreement is the game loop's fault.
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb
import numpy as np
import pandas as pd

from src.config import settings
from src.simulation.pa_model import OUTCOME_INDEX, OUTCOMES

N_LIVE_STATES = 24
TERMINAL_STATE = 24
N_STATES = 25
# A grand slam is 4, but a run can also score mid-PA on a wild pitch or a steal
# of home, so a single plate appearance can produce more than four.
MAX_RUNS = 6


def _lake() -> duckdb.DuckDBPyConnection:
    path = (settings.data_dir / "saber.duckdb").resolve()
    if not path.exists():
        raise FileNotFoundError(f"No DuckDB lake at {path}. Run `make dbt-duckdb` first.")
    return duckdb.connect(str(path), read_only=True)


@dataclass
class TransitionMatrix:
    """Dense ``P(next state, runs | state, outcome)`` plus its expected-runs solution.

    ``probs`` has shape ``(24, 9, 25, 5)``: state, outcome, next state, runs.
    """

    era: str
    probs: np.ndarray

    @classmethod
    def load(cls, era: str = "2023-2026") -> TransitionMatrix:
        con = _lake()
        try:
            df = con.sql(
                f"""
                select state_before, outcome, state_after, runs_on_play, prob
                from main_gold.gd_base_transitions
                where era = '{era}'
                """
            ).df()
        finally:
            con.close()
        if df.empty:
            raise ValueError(f"No transitions for era {era!r}")

        probs = np.zeros((N_LIVE_STATES, len(OUTCOMES), N_STATES, MAX_RUNS + 1))
        probs[
            df.state_before.to_numpy(dtype=int),
            df.outcome.map(OUTCOME_INDEX).to_numpy(dtype=int),
            df.state_after.to_numpy(dtype=int),
            df.runs_on_play.to_numpy(dtype=int),
        ] = df.prob.to_numpy(dtype=float)
        return cls(era=era, probs=probs)

    def run_expectancy(self, outcome_probs: np.ndarray) -> np.ndarray:
        """Expected runs to the end of the half-inning from each of the 24 states.

        Solves the absorbing chain exactly. ``outcome_probs`` is either one
        distribution over outcome classes, or one per state — shape ``(9,)`` or
        ``(24, 9)``. The per-state form matters: outcome rates genuinely depend on
        the base-out state, and assuming they do not biases run expectancy downward.
        """
        if outcome_probs.shape == (len(OUTCOMES),):
            per_state = np.tile(outcome_probs, (N_LIVE_STATES, 1))
        elif outcome_probs.shape == (N_LIVE_STATES, len(OUTCOMES)):
            per_state = outcome_probs
        else:
            raise ValueError(
                f"expected shape ({len(OUTCOMES)},) or ({N_LIVE_STATES}, {len(OUTCOMES)}), "
                f"got {outcome_probs.shape}"
            )

        # Marginalise the outcome away: joint[s, s', r] = sum_o P(o|s) P(s', r | s, o)
        joint = np.einsum("so,sonr->snr", per_state, self.probs)

        # Immediate expected runs from each state, and the 24x24 sub-stochastic
        # matrix over live states (dropping the absorbing column).
        runs = np.arange(MAX_RUNS + 1, dtype=float)
        immediate = np.einsum("snr,r->s", joint, runs)
        q = joint[:, :N_LIVE_STATES, :].sum(axis=2)

        return np.linalg.solve(np.eye(N_LIVE_STATES) - q, immediate)

    # Teams do not bat nine innings. The home side skips the ninth when already
    # ahead, which happens often enough to pull the real figure to 8.895 (measured
    # 2023-2026). Comparing 9 x RE(0) against actual runs per team-game therefore
    # flatters the model by about 1.2%, so the default here is the measured value.
    MEAN_INNINGS_BATTED = 8.895

    def expected_runs_per_game(
        self, outcome_probs: np.ndarray, innings: float = MEAN_INNINGS_BATTED
    ) -> float:
        """Expected runs per team-game: independent innings from state 0."""
        return innings * float(self.run_expectancy(outcome_probs)[0])


def empirical_run_expectancy(era: str = "2023-2026") -> pd.DataFrame:
    """The measured RE24 from ``gd_run_expectancy``, for comparison."""
    con = _lake()
    try:
        return con.sql(
            f"""
            select state, run_expectancy, plate_appearances
            from main_gold.gd_run_expectancy
            where era = '{era}'
            order by state
            """
        ).df()
    finally:
        con.close()


def load_plate_appearances(seasons: tuple[int, ...] | None = None) -> pd.DataFrame:
    """Plate appearances for fitting the outcome model (regular season only)."""
    where = "where season_phase = 'regular'"
    if seasons:
        where += f" and season in ({','.join(str(s) for s in seasons)})"
    con = _lake()
    try:
        return con.sql(
            f"""
            select season, game_pk, batter, pitcher, stand, p_throws,
                   outcome, state_before, runs_on_play
            from main_silver.sv_plate_appearances
            {where}
            """
        ).df()
    finally:
        con.close()
