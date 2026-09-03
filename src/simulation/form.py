"""Day-to-day pitcher form: one roll per outing, not per batter (Phases 4-5).

The engine's remaining weakness is that its games are too orderly — it produces the
right number of runs and too few blowouts and shutouts. Roughly 60% of that shortfall
is *between-inning* correlation: real innings pitched by the same man on the same
night hang together, and a model that draws fresh randomness every plate appearance
throws that away.

This module supplies the missing correlation. Before a pitcher takes the mound in a
simulated game we decide what kind of night he is having, once, and that decision
rides for every batter he faces.

Three questions, answered the same way as every other adjustment in the engine
(``docs/explainers/06-the-same-trick-four-times.md``):

**Direction — what changes?** From ``gd_pitcher_form_direction``, split on fastball
velocity rather than on how the outing went. Sorting starts by runs allowed would be
selection on the dependent variable; sorting by strikeout-minus-walk rate exaggerates
those two outcomes mechanically. Velocity is observed before any outcome and is not
one of the nine quantities being measured.

**Size — how much?** Estimated, not tuned. A pitcher's line varies night to night
mostly because 22 batters is a small sample, so the question is how much his observed
spread *exceeds* what pure chance would produce. Method of moments on that excess puts
a starter's true form at roughly 4% around his own baseline — far less than a
best-third/worst-third comparison suggests, because most of that contrast is luck.

**Mean preservation — does this smuggle in runs?** Yes, unless corrected. Runs are
convex in a pitcher's quality: a night 10% worse costs more than a night 10% better
saves, because baserunners compound. Averaging an even mix of good and bad nights
therefore *raises* scoring. The offset below removes that, so adding realism does not
quietly become adding offense.
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb
import numpy as np

from src.config import settings
from src.simulation.pa_model import OUTCOMES, PAOutcomeModel
from src.simulation.transitions import TransitionMatrix

# Outcomes that put a batter on base; the complement of K and OUT. Used to express
# form on the same scale the variance decomposition measures it on.
ON_BASE = tuple(o for o in OUTCOMES if o not in ("K", "OUT"))
_ON_BASE_MASK = np.array([o in ON_BASE for o in OUTCOMES])

# Gauss-Hermite nodes integrate a Normal cleanly in a handful of points, which keeps
# the mean-preservation solve exact rather than Monte-Carlo noisy.
_QUAD_POINTS = 15


def _lake() -> duckdb.DuckDBPyConnection:
    path = (settings.data_dir / "saber.duckdb").resolve()
    if not path.exists():
        raise FileNotFoundError(f"No DuckDB lake at {path}. Run `make dbt-duckdb` first.")
    return duckdb.connect(str(path), read_only=True)


def load_direction(era: str = "2023-2026") -> np.ndarray:
    """Log-rate direction of a bad night, ordered like ``OUTCOMES`` and centred."""
    con = _lake()
    try:
        df = con.sql(
            f"select outcome, log_ratio from main_gold.gd_pitcher_form_direction "
            f"where era = '{era}'"
        ).df()
    finally:
        con.close()
    if df.empty:
        raise ValueError(f"No pitcher form direction for era {era!r}")
    lookup = dict(zip(df.outcome, df.log_ratio, strict=True))
    d = np.array([lookup.get(o, 0.0) for o in OUTCOMES], dtype=float)
    return d - d.mean()


def estimate_form_sd(seasons: tuple[int, ...] = (2023, 2024, 2025, 2026)) -> float:
    """How much a pitcher's on-base rate allowed truly varies from night to night.

    Method of moments. A pitcher facing 22 batters will post a wide range of lines
    even with unchanging talent, so the observed spread is::

        observed variance = binomial (sampling) variance + true form variance

    Subtracting the first from the second leaves the part that is really him. The
    result is an *upper* bound, because the residual also contains opponent quality
    and park effects that are not netted out here — which is the conservative
    direction for a term whose job is to add variance.
    """
    con = _lake()
    try:
        seasons_sql = ",".join(str(s) for s in seasons)
        df = con.sql(
            f"""
            with per_game as (
                select pitcher, season, game_pk, count(*) as bf,
                       avg(case when outcome in ('K','OUT') then 0.0 else 1.0 end) as obr
                from main_silver.sv_plate_appearances
                where season_phase = 'regular' and season in ({seasons_sql})
                group by 1, 2, 3 having count(*) >= 12
            ),
            per_season as (
                select pitcher, season, avg(obr) as season_obr
                from per_game group by 1, 2 having count(*) >= 15
            )
            select g.bf, g.obr, s.season_obr
            from per_game g join per_season s using (pitcher, season)
            """
        ).df()
    finally:
        con.close()

    weights = df.bf.to_numpy(dtype=float)
    resid = (df.obr - df.season_obr).to_numpy(dtype=float)
    season_obr = df.season_obr.to_numpy(dtype=float)

    observed = np.average(resid**2, weights=weights)
    binomial = np.average(season_obr * (1 - season_obr) / weights, weights=weights)
    excess = observed - binomial
    # A non-positive excess means the data cannot distinguish form from chance; the
    # honest response is no form effect at all rather than a tiny arbitrary one.
    return float(np.sqrt(excess)) if excess > 0 else 0.0


@dataclass
class PitcherForm:
    """Per-outing random multiplier on a pitcher's outcome rates."""

    direction: np.ndarray
    sigma: float
    offset: float
    target_sd: float

    @classmethod
    def calibrate(
        cls,
        model: PAOutcomeModel,
        transitions: TransitionMatrix,
        era: str = "2023-2026",
        seasons: tuple[int, ...] = (2023, 2024, 2025, 2026),
    ) -> PitcherForm:
        d = load_direction(era)
        target_sd = estimate_form_sd(seasons)
        league = model.league_probabilities()

        def on_base_at(z: float) -> float:
            rates = league * np.exp(z * d)
            rates = rates / rates.sum()
            return float(rates[_ON_BASE_MASK].sum())

        nodes, weights = np.polynomial.hermite_e.hermegauss(_QUAD_POINTS)
        weights = weights / weights.sum()

        # Solve for the sigma whose induced spread in on-base allowed matches what the
        # variance decomposition measured. Monotone in sigma, so bisection is safe.
        def induced_sd(sigma: float) -> float:
            values = np.array([on_base_at(sigma * n) for n in nodes])
            mean = float(np.dot(weights, values))
            return float(np.sqrt(np.dot(weights, (values - mean) ** 2)))

        sigma = 0.0
        if target_sd > 0:
            lo, hi = 0.0, 5.0
            for _ in range(80):
                mid = 0.5 * (lo + hi)
                if induced_sd(mid) < target_sd:
                    lo = mid
                else:
                    hi = mid
            sigma = 0.5 * (lo + hi)

        # Mean preservation: shift the distribution so expected run scoring is
        # unchanged. Without it, convexity turns added variance into added offense.
        base_rates = model.state_probabilities()
        baseline = float(transitions.run_expectancy(base_rates)[0])

        def expected_re(offset: float) -> float:
            total = 0.0
            for node, weight in zip(nodes, weights, strict=True):
                adj = base_rates * np.exp((sigma * node + offset) * d)
                adj = adj / adj.sum(axis=1, keepdims=True)
                total += weight * float(transitions.run_expectancy(adj)[0])
            return total

        offset = 0.0
        if sigma > 0:
            lo, hi = -1.0, 1.0
            for _ in range(60):
                mid = 0.5 * (lo + hi)
                if expected_re(mid) < baseline:
                    lo = mid
                else:
                    hi = mid
            offset = 0.5 * (lo + hi)

        return cls(direction=d, sigma=sigma, offset=offset, target_sd=target_sd)

    def draw(self, rng: np.random.Generator, size: int) -> np.ndarray:
        """Draw ``size`` per-outing multipliers, shape ``(size, 9)``.

        One call per pitcher per simulated game — never per plate appearance. Drawing
        per plate appearance would add variance without adding *correlation*, which is
        the opposite of what is needed: the innings would stay strangers.
        """
        if self.sigma <= 0:
            return np.ones((size, len(OUTCOMES)))
        z = rng.normal(0.0, self.sigma, size=size) + self.offset
        return np.exp(z[:, None] * self.direction[None, :])
