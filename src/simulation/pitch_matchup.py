"""Batter-versus-pitch-family matchups (Phases 4-5).

Makes the batter term in the odds-ratio model depend on *what the pitcher throws*.
A hitter who handles velocity but chases spin is a different matchup against a
fastball-first starter than against one who lives on sliders, and nothing else in the
engine can express that.

**The signal is weak and is handled as weak.** Year-over-year persistence of a
batter's relative on-base by family is r ~ +0.16 against fastballs, +0.15 against
offspeed, and **+0.04 against breaking balls** — the last is indistinguishable from
noise. For scale, team defence repeats at +0.52 to +0.64 and clutch hitting at ~+0.04.
So most of the spread between batters is sampling error, and the shrinkage below is
estimated from that rather than assumed. "He can't hit a curveball" is far less
stable as a player trait than broadcast language suggests.

The adjustment is a single number per matchup — the pitcher's family shares weighted
into the batter's shrunk relative rates — converted into outcome multipliers along
the same axis the pitcher-form model uses, with the sign flipped: a batter having
success looks like a pitcher losing his stuff.

**This is deliberately NOT wired into the simulator, because it was measured and it
makes predictions worse.** Fitting profiles on 2023-2025 and testing on 155,912
held-out 2026 plate appearances, adding the term raised log-loss from 0.627262 to
0.627321 — a paired per-PA t of **-2.77**, significant in the wrong direction. Scanning
the adjustment scale, held-out loss rises monotonically from 0.0 upward, so the
optimal weight on this term is **zero**. The correlation between the adjustment and
actually reaching base is +0.006 in the training seasons and **-0.003 out of sample**:
the sign does not survive.

The likely reason is absorption. A slider-heavy pitcher's effectiveness with sliders
is already inside his season rate, and a batter's general ability is already inside
his; what is left over is a genuine interaction, but a small one that noise swamps.

The module and its marts are kept because the *descriptive* content is real and
useful for player analysis — "this hitter's on-base drops against offspeed" is a
legitimate scouting observation, and ``COMPANY_CONTEXT`` lists player value cards and
scouting shortlists as products. It is the *predictive* use inside the engine that the
evidence rejects. Do not enable it there without re-running the held-out test above.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import duckdb
import numpy as np

from src.config import settings
from src.simulation.form import _ON_BASE_MASK, load_direction
from src.simulation.pa_model import PAOutcomeModel

FAMILIES: tuple[str, ...] = ("fastball", "breaking", "offspeed")


def _lake() -> duckdb.DuckDBPyConnection:
    path = (settings.data_dir / "saber.duckdb").resolve()
    if not path.exists():
        raise FileNotFoundError(f"No DuckDB lake at {path}. Run `make dbt-duckdb` first.")
    return duckdb.connect(str(path), read_only=True)


def _regression_constant(rel: np.ndarray, n: np.ndarray, base: float) -> float:
    """Method-of-moments shrinkage constant for a relative rate.

    Same logic as the player-rate shrinkage in ``pa_model``: the observed spread is
    part real and part sampling noise, and the binomial variance says how much is
    noise. A non-positive remainder means the spread is fully explained by chance, so
    regress essentially all the way to zero.
    """
    if n.sum() <= 0:
        return 1e6
    observed = np.average(rel**2, weights=n)
    sampling = base * (1 - base) * np.average(1.0 / np.maximum(n, 1), weights=n)
    talent = observed - sampling
    if talent <= 0:
        return 1e6
    return max(5.0, base * (1.0 - base) / talent - 1.0)


@dataclass
class PitchMatchup:
    """Batter-by-pitch-family adjustment to the odds-ratio model."""

    direction: np.ndarray
    profiles: dict[tuple[int, str], tuple[float, float]] = field(default_factory=dict)
    mixes: dict[int, dict[str, float]] = field(default_factory=dict)
    shrink_k: dict[str, float] = field(default_factory=dict)
    league_on_base: float = 0.32
    _exponent_scale: float = 1.0

    @classmethod
    def load(
        cls,
        model: PAOutcomeModel,
        seasons: tuple[int, ...],
        era: str = "2023-2026",
    ) -> PitchMatchup:
        con = _lake()
        try:
            seasons_sql = ",".join(str(s) for s in seasons)
            prof = con.sql(
                f"""select batter, pitch_family, pa, relative_obr
                    from main_gold.gd_batter_pitch_profile where season in ({seasons_sql})"""
            ).df()
            mix = con.sql(
                f"""select pitcher, pitch_family, sum(pa) as pa
                    from main_gold.gd_pitcher_pitch_mix where season in ({seasons_sql})
                    group by 1, 2"""
            ).df()
        finally:
            con.close()

        league = model.league_probabilities()
        base = float(league[_ON_BASE_MASK].sum())

        # One shrinkage constant per family, estimated from that family's own spread.
        shrink_k: dict[str, float] = {}
        for fam in FAMILIES:
            sub = prof[prof.pitch_family == fam]
            shrink_k[fam] = _regression_constant(
                sub.relative_obr.to_numpy(dtype=float), sub.pa.to_numpy(dtype=float), base
            )

        profiles: dict[tuple[int, str], tuple[float, float]] = {}
        for r in prof.itertuples():
            k = shrink_k[r.pitch_family]
            shrunk = float(r.relative_obr) * float(r.pa) / (float(r.pa) + k)
            profiles[(int(r.batter), str(r.pitch_family))] = (shrunk, float(r.pa))

        mixes: dict[int, dict[str, float]] = {}
        for pitcher, grp in mix.groupby("pitcher"):
            total = float(grp.pa.sum())
            if total > 0:
                mixes[int(pitcher)] = {
                    str(r.pitch_family): float(r.pa) / total for r in grp.itertuples()
                }

        # Direction is the pitcher-form axis with the sign flipped: a batter having
        # success looks like a pitcher who has lost his stuff.
        direction = -load_direction(era)

        obj = cls(
            direction=direction,
            profiles=profiles,
            mixes=mixes,
            shrink_k=shrink_k,
            league_on_base=base,
        )
        obj._exponent_scale = obj._solve_scale(league)
        return obj

    def _solve_scale(self, league: np.ndarray) -> float:
        """Exponent per unit of on-base adjustment, from a local linear solve.

        The adjustment arrives as a change in on-base rate; the engine needs an
        exponent along ``direction``. Solving once at a small step and scaling keeps
        the mapping consistent with how the other multiplicative terms are calibrated.
        """
        step = 0.05
        rates = league * np.exp(step * self.direction)
        rates = rates / rates.sum()
        delta = float(rates[_ON_BASE_MASK].sum()) - self.league_on_base
        return step / delta if abs(delta) > 1e-12 else 0.0

    def adjustment(self, batter: int, pitcher: int) -> float:
        """Expected on-base adjustment for this matchup; 0.0 when either is unknown."""
        mix = self.mixes.get(pitcher)
        if mix is None:
            return 0.0
        total = 0.0
        for fam, share in mix.items():
            entry = self.profiles.get((batter, fam))
            if entry is not None:
                total += share * entry[0]
        return total

    def multiplier(self, batters: np.ndarray, pitchers: np.ndarray) -> np.ndarray:
        """Outcome multipliers for a vector of matchups, shape ``(n, 9)``."""
        adj = np.array(
            [self.adjustment(int(b), int(p)) for b, p in zip(batters, pitchers, strict=True)]
        )
        return np.exp((adj * self._exponent_scale)[:, None] * self.direction[None, :])
