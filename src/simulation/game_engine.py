"""Plate-appearance-level Monte-Carlo game + season engine — Phases 4–5 (stub).

Rolls games forward one PA at a time using player rate inputs, then aggregates to
season outcomes (playoff / World Series odds) via Monte-Carlo over the remaining
schedule. Supports **counterfactuals** by swapping a player's rate inputs and
re-simulating (PROJECT_CONTEXT §7). Implement in Phases 4–5.

Design notes:
  * State = (inning, half, outs, base configuration, score).
  * Transition probabilities come from Bayesian player rates (partial-pooled).
  * A Markov/MC roll yields a final-score distribution per game.
  * Season sim resamples each remaining game many times for playoff odds.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GameState:
    """Immutable plate-appearance game state."""

    inning: int = 1
    top: bool = True
    outs: int = 0
    bases: tuple[bool, bool, bool] = (False, False, False)  # 1B, 2B, 3B
    home_score: int = 0
    away_score: int = 0


def simulate_game(home_rates: dict, away_rates: dict, n_sims: int = 1000) -> dict:
    """Monte-Carlo a single game; return a final-score distribution. (Phase 4)"""
    raise NotImplementedError("PA-level game simulation lands in Phase 4.")


def simulate_season(schedule, team_rates: dict, n_sims: int = 10000) -> dict:
    """Monte-Carlo the remaining schedule → playoff/WS odds. (Phase 4)"""
    raise NotImplementedError("Season simulation lands in Phase 4.")


def counterfactual(base_rates: dict, perturbation: dict) -> dict:
    """Apply a perturbation to player rates and return modified inputs. (Phase 5)"""
    raise NotImplementedError("Counterfactual perturbation lands in Phase 5.")
