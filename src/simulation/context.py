"""Per-game run environment: park and weather as outcome multipliers (Phases 4-5).

A park factor says Coors yields 27% more runs than league. It does not say *how*, and
a simulator needs the how — the mean is only half of what Phase 4 depends on, since
playoff odds live in the tails of the score distribution.

Two pieces:

* **Direction** (`gd_run_env_direction`) — measured, not assumed. A richer run
  environment converts strikeouts into extra-base hits: triples move most (+0.20 in
  log rate), then home runs (+0.15) and doubles (+0.11), while strikeouts fall
  (-0.06). An assumed direction such as "scale all hits equally" would reproduce the
  right mean runs with the wrong distribution shape, which defeats the purpose.

* **Magnitude** — calibrated against the analytic Markov solve rather than guessed.
  For a target multiplier ``m`` we find the exponent ``a`` such that moving league
  rates by ``exp(a * direction)`` makes run expectancy exactly ``m`` times baseline.
  Because that solve is the same one used to validate the transition matrix, the
  calibration is consistent with everything else in the engine by construction.

The multiplier is shared by both teams — they play the same park in the same
weather — so it does not move win probability directly. What it does is spread the
run distribution across games, which is precisely the under-dispersion it was wired
in to fix.
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb
import numpy as np

from src.config import settings
from src.simulation.pa_model import OUTCOMES, PAOutcomeModel
from src.simulation.transitions import TransitionMatrix

# Range of run environments to calibrate over. Observed park x weather products in
# 2025 span 0.777 (a cold night with the wind in at a pitcher's park) to 1.538 (a hot
# evening at Coors with the wind blowing out), so the grid brackets the real range
# with margin. Outside it `outcome_multiplier` clips rather than extrapolates.
MIN_MULTIPLIER = 0.65
MAX_MULTIPLIER = 1.65
GRID_POINTS = 41


def _lake() -> duckdb.DuckDBPyConnection:
    path = (settings.data_dir / "saber.duckdb").resolve()
    if not path.exists():
        raise FileNotFoundError(f"No DuckDB lake at {path}. Run `make dbt-duckdb` first.")
    return duckdb.connect(str(path), read_only=True)


def load_direction(era: str = "2023-2026") -> np.ndarray:
    """Log-rate direction of a richer run environment, ordered like ``OUTCOMES``."""
    con = _lake()
    try:
        df = con.sql(
            f"select outcome, log_ratio from main_gold.gd_run_env_direction where era = '{era}'"
        ).df()
    finally:
        con.close()
    if df.empty:
        raise ValueError(f"No run-environment direction for era {era!r}")
    lookup = dict(zip(df.outcome, df.log_ratio, strict=True))
    direction = np.array([lookup.get(o, 0.0) for o in OUTCOMES], dtype=float)
    # Centre it: a pure level shift is absorbed by renormalisation anyway, and
    # centring keeps the exponent interpretable as "how far along this direction".
    return direction - direction.mean()


@dataclass
class RunEnvironment:
    """Maps a per-game run multiplier to a multiplicative outcome adjustment."""

    direction: np.ndarray
    multipliers: np.ndarray
    exponents: np.ndarray

    @classmethod
    def calibrate(
        cls,
        model: PAOutcomeModel,
        transitions: TransitionMatrix,
        era: str = "2023-2026",
        direction: np.ndarray | None = None,
    ) -> RunEnvironment:
        """Solve for the exponent that hits each target multiplier on the grid."""
        d = load_direction(era) if direction is None else direction
        base_rates = model.state_probabilities()
        baseline = float(transitions.run_expectancy(base_rates)[0])

        def run_expectancy_at(exponent: float) -> float:
            adjusted = base_rates * np.exp(exponent * d)
            adjusted = adjusted / adjusted.sum(axis=1, keepdims=True)
            return float(transitions.run_expectancy(adjusted)[0])

        targets = np.linspace(MIN_MULTIPLIER, MAX_MULTIPLIER, GRID_POINTS)
        exponents = np.empty_like(targets)
        for i, m in enumerate(targets):
            want = m * baseline
            # Run expectancy is monotonic in the exponent, so bisection is safe and
            # needs no derivative of a matrix solve.
            lo, hi = -6.0, 6.0
            for _ in range(60):
                mid = 0.5 * (lo + hi)
                if run_expectancy_at(mid) < want:
                    lo = mid
                else:
                    hi = mid
            exponents[i] = 0.5 * (lo + hi)
        return cls(direction=d, multipliers=targets, exponents=exponents)

    def outcome_multiplier(self, run_multiplier: float) -> np.ndarray:
        """Multiplicative adjustment to outcome rates for this run environment.

        Clipped to the calibrated range rather than extrapolated: beyond it the
        exponent solve is unverified, and a simulator that silently extrapolates a
        park factor is inventing baseball.
        """
        m = float(np.clip(run_multiplier, self.multipliers[0], self.multipliers[-1]))
        exponent = float(np.interp(m, self.multipliers, self.exponents))
        return np.exp(exponent * self.direction)


def load_game_environments(season: int, era: str = "2023-2026") -> dict[int, float]:
    """Park x weather run multiplier per ``game_pk`` for one season."""
    con = _lake()
    try:
        df = con.sql(
            f"""
            select g.game_pk,
                   coalesce(pf.park_factor, 1.0) * coalesce(wf.weather_factor, 1.0) as env
            from main_silver.sv_games g
            left join main_gold.gd_park_factor pf
              on pf.season = g.season and pf.venue_id = g.venue_id
            left join main_gold.gd_weather_factor wf
              on wf.era = '{era}'
             and wf.is_indoor = g.is_indoor
             and wf.wind_out_sign = case when g.is_indoor then 0
                                         else coalesce(g.wind_out_sign, 0) end
             and wf.temp_band = case
                    when g.is_indoor then 0
                    when g.weather_temp_f < 50 then 40
                    when g.weather_temp_f < 60 then 50
                    when g.weather_temp_f < 70 then 60
                    when g.weather_temp_f < 80 then 70
                    when g.weather_temp_f < 90 then 80
                    else 90 end
            where g.season = {season} and g.season_phase = 'regular'
            """
        ).df()
    finally:
        con.close()
    return {int(r.game_pk): float(r.env) for r in df.itertuples()}
