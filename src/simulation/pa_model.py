"""Plate-appearance outcome model — the odds-ratio method (Phases 4-5).

Gives ``P(outcome | batter, pitcher)`` over the nine outcome classes in
``sv_plate_appearances``. This is the first half of the simulation engine; the other
half is ``src.simulation.transitions``, which turns an outcome into a new base-out
state and runs.

**Why the odds-ratio method rather than a Bayesian hierarchical model.**
``PROJECT_CONTEXT`` §7 proposes PyMC partial pooling. Two reasons not to, both
practical rather than theoretical. First, ``pymc`` and ``bambi`` do not currently
import in this project (an ArviZ API break), so it is a dependency fix before it is a
model. Second, and more importantly, every experiment in Phase 3 found that added
model capacity *cost* accuracy on this data — the gradient booster lost to Elo while
containing Elo as a feature. The odds-ratio method (Tango) is the standard, robust
way to combine a batter rate and a pitcher rate against a league baseline, and it has
no sampler, no convergence diagnostics, and nothing to overfit.

The combination, per outcome ``o``::

    raw(o) = batter_rate(o) * pitcher_rate(o) * state_rate(o) / league_rate(o)^2
    P(o)   = raw(o) / sum over outcomes of raw

The **state** term is not decoration. Outcome rates are strongly conditional on the
base-out state — the walk rate ranges from 0.057 to 0.150 against a league 0.085, and
peaks exactly where baseball says it should: runners on second and third with two
out, first base open, so the pitcher works around the batter. Treating outcomes as
independent of state (the plain Markov assumption) understates run expectancy by
roughly 3% and does so unevenly, concentrated in the very states that decide close
games. Each extra multiplicative term divides by the league rate once more, which is
what keeps the odds ratio dimensionally consistent.

Both player rates are shrunk toward the league rate first, by empirical Bayes with a
beta-binomial method-of-moments estimate of the regression constant. Shrinkage is not
optional here: 137 of 674 batters in a season have fewer than 50 plate appearances,
and their raw rates are mostly sampling noise.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# Order is fixed and load-bearing: probability vectors and the transition matrix are
# indexed by it, so appending rather than inserting is the only safe way to extend.
OUTCOMES: tuple[str, ...] = ("K", "BB", "HBP", "1B", "2B", "3B", "HR", "ROE", "OUT")
OUTCOME_INDEX: dict[str, int] = {o: i for i, o in enumerate(OUTCOMES)}

_MIN_K = 5.0  # floor on the regression constant; see _regression_constant


def _regression_constant(rates: np.ndarray, n: np.ndarray, league: float) -> float:
    """Beta-binomial method-of-moments estimate of the shrinkage constant ``k``.

    ``k`` is "how many plate appearances of league-average performance to add before
    believing a player's own rate". Derived rather than assumed: the spread of
    observed player rates is part real talent and part sampling noise, and the
    binomial variance tells us how much of it is noise. What is left is talent
    spread, and ``k = mu(1-mu)/sigma^2 - 1``.

    Falls back to a large constant when the estimated talent variance is zero or
    negative — that means the observed spread is entirely explicable as noise, so the
    honest response is to trust the league rate almost completely rather than to
    divide by something near zero.
    """
    total_n = n.sum()
    if total_n <= 0 or not (0.0 < league < 1.0):
        return 1e6

    observed_var = np.average((rates - league) ** 2, weights=n)
    sampling_var = league * (1.0 - league) * np.average(1.0 / np.maximum(n, 1), weights=n)
    talent_var = observed_var - sampling_var
    if talent_var <= 0:
        return 1e6
    return max(_MIN_K, league * (1.0 - league) / talent_var - 1.0)


def _shrunk_rates(counts: pd.DataFrame, league: pd.Series) -> tuple[pd.DataFrame, dict[str, float]]:
    """Shrink each player's per-outcome rate toward the league rate."""
    n = counts.sum(axis=1).to_numpy(dtype=float)
    out = pd.DataFrame(index=counts.index, columns=list(OUTCOMES), dtype=float)
    ks: dict[str, float] = {}
    for outcome in OUTCOMES:
        lg = float(league[outcome])
        raw = counts[outcome].to_numpy(dtype=float) / np.maximum(n, 1)
        k = _regression_constant(raw, n, lg)
        ks[outcome] = k
        out[outcome] = (counts[outcome].to_numpy(dtype=float) + lg * k) / (n + k)
    # Shrinking each outcome independently leaves rows summing to ~1 but not exactly;
    # renormalise so every row is a genuine distribution.
    return out.div(out.sum(axis=1), axis=0), ks


@dataclass
class PAOutcomeModel:
    """``P(outcome | batter, pitcher)`` by the odds-ratio method."""

    league: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    batters: pd.DataFrame = field(default_factory=pd.DataFrame)
    pitchers: pd.DataFrame = field(default_factory=pd.DataFrame)
    states: pd.DataFrame = field(default_factory=pd.DataFrame)
    batter_k: dict[str, float] = field(default_factory=dict)
    pitcher_k: dict[str, float] = field(default_factory=dict)

    def fit(self, pa: pd.DataFrame) -> PAOutcomeModel:
        """Fit from a frame of plate appearances (``batter``, ``pitcher``, ``outcome``)."""
        missing = {"batter", "pitcher", "outcome"} - set(pa.columns)
        if missing:
            raise ValueError(f"plate appearances frame missing columns: {sorted(missing)}")

        counts = pd.crosstab(pa["batter"], pa["outcome"]).reindex(
            columns=list(OUTCOMES), fill_value=0
        )
        pitcher_counts = pd.crosstab(pa["pitcher"], pa["outcome"]).reindex(
            columns=list(OUTCOMES), fill_value=0
        )
        totals = counts.sum(axis=0)
        self.league = totals / totals.sum()
        self.batters, self.batter_k = _shrunk_rates(counts, self.league)
        self.pitchers, self.pitcher_k = _shrunk_rates(pitcher_counts, self.league)

        if "state_before" in pa.columns:
            state_counts = pd.crosstab(pa["state_before"], pa["outcome"]).reindex(
                index=range(24), columns=list(OUTCOMES), fill_value=0
            )
            self.states, _ = _shrunk_rates(state_counts, self.league)
        return self

    def league_probabilities(self) -> np.ndarray:
        return self.league.reindex(list(OUTCOMES)).to_numpy(dtype=float)

    def state_probabilities(self) -> np.ndarray:
        """Per-state league outcome rates, shape ``(24, 9)``; league rate if unfitted."""
        lg = self.league_probabilities()
        if self.states.empty:
            return np.tile(lg, (24, 1))
        return self.states.to_numpy(dtype=float)

    def probabilities(
        self, batter: int | None, pitcher: int | None, state: int | None = None
    ) -> np.ndarray:
        """Outcome distribution for one matchup, optionally in a base-out state.

        An unknown player falls back to the league rate rather than raising: a
        simulation of a future season will contain call-ups who have no history, and
        refusing to price them would be worse than pricing them as average.
        """
        lg = self.league_probabilities()
        b = (
            self.batters.loc[batter].to_numpy(dtype=float)
            if batter is not None and batter in self.batters.index
            else lg
        )
        p = (
            self.pitchers.loc[pitcher].to_numpy(dtype=float)
            if pitcher is not None and pitcher in self.pitchers.index
            else lg
        )
        raw = b * p / np.maximum(lg, 1e-12)
        if state is not None and not self.states.empty:
            raw = raw * self.states.to_numpy(dtype=float)[state] / np.maximum(lg, 1e-12)
        total = raw.sum()
        return lg.copy() if total <= 0 else raw / total

    def probability_matrix(
        self, batters: np.ndarray, pitchers: np.ndarray, states: np.ndarray | None = None
    ) -> np.ndarray:
        """Vectorised matchup probabilities, shape ``(len(batters), 9)``.

        The simulator draws billions of plate appearances, so the per-matchup path
        above is only for inspection; this is what the engine calls.
        """
        lg = self.league_probabilities()
        b = self.batters.reindex(batters).to_numpy(dtype=float)
        p = self.pitchers.reindex(pitchers).to_numpy(dtype=float)
        b = np.where(np.isnan(b), lg, b)
        p = np.where(np.isnan(p), lg, p)
        raw = b * p / np.maximum(lg, 1e-12)
        if states is not None and not self.states.empty:
            raw = raw * self.states.to_numpy(dtype=float)[states] / np.maximum(lg, 1e-12)
        return raw / raw.sum(axis=1, keepdims=True)
