"""Score-based outcome models: predict runs, then derive the win (Phase 3-4).

Every other model in this project predicts a *binary* outcome directly. These predict
how many runs each side scores and integrate to a win probability, which buys two
things nothing else here provides:

* **A score distribution.** ``P(final score is 4-2)``, not just ``P(home wins)``. That
  is a product in its own right (game and series breakdowns) and, until now, only the
  plate-appearance simulator could produce it — at a much higher cost and, measured
  head to head, worse discrimination.
* **A dispersion parameter that is fitted rather than fought.** Six experiments failed
  to explain the simulator's under-dispersion with covariates. A negative binomial
  does not need to explain it: the overdispersion is a parameter, estimated from the
  residuals.

The mean model is the standard offence/defence decomposition used for football and
soccer scorelines (Maher, Dixon-Coles)::

    log E[runs by team i against j] = intercept + offence_i + defence_j + home

Runs are counts, so Poisson is the natural starting point — but baseball run scoring
is famously more variable than Poisson, because runs arrive in clusters rather than
independently. The negative binomial variant estimates that extra variance instead of
assuming it away.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from src.models.baselines import _check_columns

MAX_RUNS = 30  # P(a team scores more) is ~1e-6; the tail is not worth carrying


def _long_format(games: pd.DataFrame) -> pd.DataFrame:
    """One row per team-game: runs scored, by whom, against whom, home or away."""
    home = pd.DataFrame(
        {
            "team": games["home_team_id"].astype(int),
            "opponent": games["away_team_id"].astype(int),
            "runs": games["home_score"],
            "is_home": 1.0,
            "game_date": games["game_date"],
        }
    )
    away = pd.DataFrame(
        {
            "team": games["away_team_id"].astype(int),
            "opponent": games["home_team_id"].astype(int),
            "runs": games["away_score"],
            "is_home": 0.0,
            "game_date": games["game_date"],
        }
    )
    return pd.concat([home, away], ignore_index=True).dropna(subset=["runs"])


@dataclass
class PoissonScoreModel:
    """Offence/defence Poisson regression on runs, integrated to a win probability."""

    name: str = "poisson_score"
    half_life_days: float = 365.0
    alpha: float = 1e-3  # ridge penalty on the offence/defence coefficients
    # Dispersion is always *estimated* so the two variants can be compared on the same
    # fit, but only the negative-binomial variant *uses* it. Without this flag the two
    # classes would be identical and the comparison meaningless.
    use_dispersion: bool = False
    dispersion: float = 0.0
    offence: dict[int, float] = field(default_factory=dict)
    defence: dict[int, float] = field(default_factory=dict)
    intercept: float = 0.0
    home_effect: float = 0.0

    def fit(self, games: pd.DataFrame) -> PoissonScoreModel:
        from sklearn.linear_model import PoissonRegressor

        _check_columns(games)
        long = _long_format(games.dropna(subset=["home_win"]))
        teams = sorted(set(long["team"]) | set(long["opponent"]))
        index = {t: i for i, t in enumerate(teams)}
        n = len(teams)

        # [offence dummies | defence dummies | home]
        design = np.zeros((len(long), 2 * n + 1))
        rows = np.arange(len(long))
        design[rows, [index[int(t)] for t in long["team"]]] = 1.0
        design[rows, [n + index[int(t)] for t in long["opponent"]]] = 1.0
        design[:, -1] = long["is_home"].to_numpy(dtype=float)

        dates = pd.to_datetime(long["game_date"])
        age = (dates.max() - dates).dt.days.to_numpy(dtype=float)
        weights = np.exp(-np.log(2.0) * age / self.half_life_days)

        model = PoissonRegressor(alpha=self.alpha, max_iter=1000, fit_intercept=True)
        model.fit(design, long["runs"].to_numpy(dtype=float), sample_weight=weights)

        coefs = model.coef_
        off = coefs[:n] - coefs[:n].mean()
        deff = coefs[n : 2 * n] - coefs[n : 2 * n].mean()
        self.offence = {int(t): float(off[i]) for t, i in index.items()}
        self.defence = {int(t): float(deff[i]) for t, i in index.items()}
        self.home_effect = float(coefs[-1])
        self.intercept = float(model.intercept_ + coefs[:n].mean() + coefs[n : 2 * n].mean())

        # Method-of-moments overdispersion: Var = mu + dispersion * mu^2. Runs cluster,
        # so this comes out clearly positive and is the whole point of the NB variant.
        mu = np.exp(design @ coefs + model.intercept_)
        resid = long["runs"].to_numpy(dtype=float) - mu
        excess = np.average(resid**2 - mu, weights=weights)
        mu2 = np.average(mu**2, weights=weights)
        self.dispersion = float(max(excess / mu2, 0.0)) if mu2 > 0 else 0.0
        return self

    def _means(self, games: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        home = games["home_team_id"].astype(int)
        away = games["away_team_id"].astype(int)
        off_h = np.array([self.offence.get(int(t), 0.0) for t in home])
        def_a = np.array([self.defence.get(int(t), 0.0) for t in away])
        off_a = np.array([self.offence.get(int(t), 0.0) for t in away])
        def_h = np.array([self.defence.get(int(t), 0.0) for t in home])
        mu_home = np.exp(self.intercept + off_h + def_a + self.home_effect)
        mu_away = np.exp(self.intercept + off_a + def_h)
        return mu_home, mu_away

    def _pmf(self, mu: np.ndarray) -> np.ndarray:
        """Run distribution per game, shape ``(n_games, MAX_RUNS + 1)``."""
        k = np.arange(MAX_RUNS + 1)
        if not self.use_dispersion or self.dispersion <= 0:
            from scipy.stats import poisson

            return np.asarray(poisson.pmf(k[None, :], mu[:, None]), dtype=float)
        from scipy.stats import nbinom

        r = 1.0 / self.dispersion
        p = r / (r + mu)
        return np.asarray(nbinom.pmf(k[None, :], r, p[:, None]), dtype=float)

    def score_distribution(self, games: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        """Independent run distributions for the two sides."""
        mu_home, mu_away = self._means(games)
        return self._pmf(mu_home), self._pmf(mu_away)

    def predict_proba(self, games: pd.DataFrame) -> np.ndarray:
        """P(home wins) by integrating the two run distributions.

        Ties are impossible in baseball, so the probability of an equal score is
        redistributed in proportion rather than dropped — extra innings are, in
        effect, a coin flip weighted by the two teams' scoring rates.
        """
        _check_columns(games)
        ph, pa = self.score_distribution(games)
        cum_away = np.cumsum(pa, axis=1)
        # P(home > away) = sum_k P(home = k) * P(away < k)
        strictly_below = np.concatenate([np.zeros((len(games), 1)), cum_away[:, :-1]], axis=1)
        p_home = (ph * strictly_below).sum(axis=1)
        p_tie = (ph * pa).sum(axis=1)
        p_away = 1.0 - p_home - p_tie
        total = p_home + p_away
        return np.where(total > 0, p_home + p_tie * p_home / np.maximum(total, 1e-12), 0.5)


@dataclass
class NegativeBinomialScoreModel(PoissonScoreModel):
    """Same mean model, with the overdispersion in run scoring estimated and used."""

    name: str = "negbin_score"
    use_dispersion: bool = True
