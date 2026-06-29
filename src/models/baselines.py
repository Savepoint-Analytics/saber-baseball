"""Baseline game-outcome models — Phase 3 (stub).

Per PROJECT_CONTEXT §7, baselines come first and set the bar that the gradient-boosted
and Bayesian models must beat on log-loss + calibration:

  * Home-field prior — constant P(home win).
  * Pythagorean expectation — win% from runs scored / allowed.
  * Log5 — matchup win prob from two teams' win rates (Bill James).
  * Elo — sequential team-strength ratings.

Each baseline exposes ``fit`` / ``predict_proba`` so they slot into the same backtest
+ calibration harness as the ML models. Implement in Phase 3.
"""

from __future__ import annotations

import numpy as np


def pythagorean_expectation(runs_for: float, runs_against: float, exp: float = 1.83) -> float:
    """Pythagorean win expectation. The 1.83 exponent is the common MLB fit.

    This one is simple and deterministic, so it is implemented now as a reference
    baseline; the model wrappers around it land in Phase 3.
    """
    rf, ra = float(runs_for), float(runs_against)
    denom = rf**exp + ra**exp
    return 0.5 if denom == 0 else rf**exp / denom


def log5(p_a: float, p_b: float) -> float:
    """Log5 probability that team A beats team B given their win rates."""
    num = p_a - p_a * p_b
    den = p_a + p_b - 2 * p_a * p_b
    return 0.5 if den == 0 else num / den


def elo_update(
    rating_a: float, rating_b: float, a_won: bool, k: float = 20.0, home_adv: float = 0.0
) -> tuple[float, float]:
    """Single Elo update. ``home_adv`` is added to team A's rating for expectation."""
    exp_a = 1.0 / (1.0 + 10 ** (-((rating_a + home_adv) - rating_b) / 400.0))
    score_a = 1.0 if a_won else 0.0
    delta = k * (score_a - exp_a)
    return rating_a + delta, rating_b - delta


def home_field_prior(home_wins: int, total_games: int) -> float:
    """Empirical P(home win) prior with a light Beta(1,1) smoothing."""
    return (home_wins + 1) / (total_games + 2) if total_games >= 0 else 0.5


def expected_log_loss(probs: np.ndarray, outcomes: np.ndarray) -> float:
    """Mean binary log-loss — the primary metric baselines are judged on. (Phase 3)"""
    raise NotImplementedError("Backtest harness lands in Phase 3.")
