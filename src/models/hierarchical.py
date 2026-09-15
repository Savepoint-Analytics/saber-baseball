"""Hierarchical Bayesian team strengths (PROJECT_CONTEXT §7).

A Bradley-Terry model where the spread of team strengths is *learned* rather than
fixed by a ridge penalty someone chose. ``sigma`` is itself estimated, so the amount
of shrinkage applied to each club's rating comes from how different the clubs
actually are that season — strong pooling in a compressed league, weak pooling in a
lopsided one.

That is the theoretical appeal, and it targets this project's binding constraint
directly: every experiment here has been limited by variance rather than bias, and
partial pooling is variance reduction with a principled amount.

**A fairness note that matters for reading the result.** Sampling takes ~1 minute per
fit, so this model is fitted once per season and holds one rating from opening day to
game 162. Elo and the refitting Bradley-Terry both update *during* the season, and
that difference alone is worth ~0.005 log-loss — more than the gap between most
models here. The honest comparison is therefore against **static** Bradley-Terry
(0.6843), not against Elo. Making it competitive with Elo would need variational
refits or an online update on top of the posterior mean.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from src.models.baselines import _check_columns, _expit


@dataclass
class HierarchicalBradleyTerry:
    """Team strengths with the pooling strength estimated from the data."""

    name: str = "hierarchical_bt"
    half_life_days: float = 365.0
    draws: int = 400
    tune: int = 400
    chains: int = 2
    seed: int = 0
    ratings: dict[int, float] = field(default_factory=dict)
    home_advantage: float = 0.0
    strength_sigma: float = 0.0

    def fit(self, games: pd.DataFrame) -> HierarchicalBradleyTerry:
        import pymc as pm

        _check_columns(games)
        played = games.dropna(subset=["home_win"])
        teams = sorted(
            set(played["home_team_id"].astype(int)) | set(played["away_team_id"].astype(int))
        )
        index = {t: i for i, t in enumerate(teams)}
        home = np.array([index[int(t)] for t in played["home_team_id"]])
        away = np.array([index[int(t)] for t in played["away_team_id"]])
        outcomes = played["home_win"].to_numpy(dtype=int)

        dates = pd.to_datetime(played["game_date"])
        age = (dates.max() - dates).dt.days.to_numpy(dtype=float)
        weights = np.exp(-np.log(2.0) * age / self.half_life_days)

        with pm.Model():
            sigma = pm.HalfNormal("sigma", 1.0)
            strength = pm.Normal("strength", 0.0, sigma, shape=len(teams))
            hfa = pm.Normal("hfa", 0.0, 0.5)
            eta = strength[home] - strength[away] + hfa
            # Recency enters as a weighted log-likelihood rather than by dropping old
            # games: a Potential lets each observation count fractionally, which is
            # what "a game from two years ago still tells us something" means.
            logp = pm.logp(pm.Bernoulli.dist(logit_p=eta), outcomes)
            pm.Potential("weighted_likelihood", (weights * logp).sum())

            idata = pm.sample(
                self.draws,
                tune=self.tune,
                chains=self.chains,
                cores=self.chains,
                progressbar=False,
                random_seed=self.seed,
                compute_convergence_checks=False,
            )

        posterior = idata.posterior
        means = np.asarray(posterior["strength"].mean(dim=("chain", "draw")).to_numpy())
        means = means - means.mean()
        self.ratings = {int(t): float(means[i]) for t, i in index.items()}
        self.home_advantage = float(posterior["hfa"].mean().to_numpy())
        self.strength_sigma = float(posterior["sigma"].mean().to_numpy())
        return self

    def predict_proba(self, games: pd.DataFrame) -> np.ndarray:
        _check_columns(games)
        home = np.array([self.ratings.get(int(t), 0.0) for t in games["home_team_id"]])
        away = np.array([self.ratings.get(int(t), 0.0) for t in games["away_team_id"]])
        return np.asarray(_expit(home - away + self.home_advantage), dtype=float)
