"""Plate-appearance game simulator — the loop over the validated components.

Assembles ``pa_model`` (what happens in a plate appearance), ``transitions`` (what
that does to the bases and the scoreboard) and ``lineup`` (who bats, who pitches)
into complete games, and Monte-Carlos them for score distributions and win
probabilities. This is what Phases 4-5 need: playoff odds and counterfactuals are
impossible from a single win probability, no matter how accurate.

**Vectorised across simulations, not across plate appearances.** Every simulation of
a matchup advances one PA in lockstep, with finished half-innings masked out. A
per-PA Python loop over a full backtest is roughly 1.3 billion iterations — days of
compute; in lockstep it is a few hundred vectorised steps per game and runs in
seconds. The cost is that the code reads as array bookkeeping rather than as
baseball, which is why the state layout is spelled out below.

**Per-simulation state**, all arrays of length ``n_sims``:

* ``state`` — base-out state 0-23, or 24 meaning the half-inning is over.
* ``score[team]`` — runs, team 0 = away, 1 = home.
* ``pa_index[team]`` — plate appearances taken, which drives the batting order.
* ``pitcher[team]`` — index into that team's pitcher pool (slot 0 is the starter).
* ``pitcher_bf[team]`` — batters faced by the current pitcher.
* ``used[team]`` — ``(n_sims, pool)`` bool, so a reliever is not reused in one game.

Rules that are easy to get wrong and are handled explicitly: the home team does not
bat in the ninth or later when already ahead; a bottom-half rally stops the moment
the home team takes the lead (walk-off); and from 2020 extra innings start with a
runner on second.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from src.simulation.context import RunEnvironment
from src.simulation.lineup import (
    BF_BUCKETS,
    LINEUP_SIZE,
    MAX_TTO,
    Bullpen,
    Lineup,
    SubstitutionPolicy,
)
from src.simulation.pa_model import OUTCOMES, PAOutcomeModel
from src.simulation.transitions import MAX_RUNS, TERMINAL_STATE, TransitionMatrix

AWAY, HOME = 0, 1
N_RUN_VALUES = MAX_RUNS + 1
REGULATION_INNINGS = 9
# The state with a runner on second and nobody out: extra innings start here from
# 2020 onward. Base code 2 is second base; outs 0.
GHOST_RUNNER_STATE = 2


@dataclass(frozen=True)
class TeamSetup:
    """One team's inputs to a simulated game."""

    lineup: Lineup
    starter: int
    bullpen: Bullpen

    @property
    def pitcher_pool(self) -> np.ndarray:
        """Starter first, then the bullpen — index 0 is always the starter."""
        return np.concatenate(([self.starter], self.bullpen.pitchers))


@dataclass
class GameResult:
    """Simulated final scores for one matchup."""

    home_score: np.ndarray
    away_score: np.ndarray

    @property
    def n_sims(self) -> int:
        return len(self.home_score)

    def home_win_probability(self) -> float:
        """Ties are impossible: the loop plays extra innings until someone wins."""
        return float((self.home_score > self.away_score).mean())

    def mean_total_runs(self) -> float:
        return float((self.home_score + self.away_score).mean())

    def score_distribution(self) -> dict[tuple[int, int], float]:
        """P(exact final score), the thing a single win probability cannot express."""
        pairs, counts = np.unique(
            np.stack([self.home_score, self.away_score], axis=1), axis=0, return_counts=True
        )
        return {
            (int(h), int(a)): float(c / self.n_sims)
            for (h, a), c in zip(pairs, counts, strict=True)
        }


def _hazard_table(policy: SubstitutionPolicy) -> np.ndarray:
    """Flatten the removal policy into ``(2, MAX_TTO+1, len(BF_BUCKETS))`` for indexing.

    Looking the policy up through a dict inside the inner loop would cost more than
    the simulation itself; this is the same numbers in a form numpy can index with
    integer arrays.
    """
    table = np.full((2, MAX_TTO + 1, len(BF_BUCKETS)), policy.fallback)
    for starter in (False, True):
        for tto in range(1, MAX_TTO + 1):
            for bi, bucket in enumerate(BF_BUCKETS):
                table[int(starter), tto, bi] = policy.removal_probability(starter, tto, bucket)
    return table


@dataclass
class GameSimulator:
    """Monte-Carlo a matchup, plate appearance by plate appearance."""

    pa_model: PAOutcomeModel
    transitions: TransitionMatrix
    policy: SubstitutionPolicy
    # Park and weather as a shared per-game run environment. Optional so the engine
    # still runs on a lake without the context marts, and so the effect of adding it
    # can be measured against the same engine without.
    run_environment: RunEnvironment | None = None
    ghost_runner_from_inning: int | None = 10
    max_innings: int = 30
    _hazard: np.ndarray = field(init=False)
    _trans_cdf: np.ndarray = field(init=False)

    def __post_init__(self) -> None:
        self._hazard = _hazard_table(self.policy)
        # (24, 9, 25 * 7) cumulative distribution over flattened (next state, runs),
        # precomputed once so each step is a comparison rather than a normalisation.
        flat = self.transitions.probs.reshape(24, len(OUTCOMES), -1)
        self._trans_cdf = np.cumsum(flat, axis=-1)

    # -- per-matchup rate tables -------------------------------------------
    def _rate_tables(self, setup: TeamSetup) -> tuple[np.ndarray, np.ndarray]:
        """Batter rates by lineup slot and pitcher rates by pool index.

        The shrunk rates are read directly rather than through
        ``PAOutcomeModel.probability_matrix``, because that helper already folds a
        pitcher in and the loop needs the two terms separately to combine them with
        the state term. Unknown players fall back to league — a simulated season
        contains call-ups with no history.
        """
        lg = self.pa_model.league_probabilities()
        bat = self.pa_model.batters.reindex(list(setup.lineup.batters)).to_numpy(dtype=float)
        pit = self.pa_model.pitchers.reindex(setup.pitcher_pool).to_numpy(dtype=float)
        return np.where(np.isnan(bat), lg, bat), np.where(np.isnan(pit), lg, pit)

    # -- the loop -----------------------------------------------------------
    def simulate(
        self,
        home: TeamSetup,
        away: TeamSetup,
        n_sims: int = 1000,
        rng: np.random.Generator | None = None,
        run_environment: float = 1.0,
    ) -> GameResult:
        """Simulate one matchup.

        ``run_environment`` is the park x weather run multiplier for this game (1.0 is
        neutral). Both teams share it — they play the same park in the same weather —
        so it does not move win probability directly. What it does is widen the spread
        of scores across games, which is what the engine was under-doing.
        """
        rng = rng or np.random.default_rng()
        lg = self.pa_model.league_probabilities()
        state_rates = self.pa_model.state_probabilities()
        # Home-field advantage enters as a fourth odds-ratio term, so each extra
        # factor divides by the league rate once more.
        side_rates = self.pa_model.side_probabilities()
        lg3 = np.maximum(lg, 1e-12) ** 3
        # A pure multiplier on rates rather than an odds-ratio term, so it is applied
        # after the division by league and before renormalisation.
        env_mult = (
            self.run_environment.outcome_multiplier(run_environment)
            if self.run_environment is not None
            else 1.0
        )

        setups = (away, home)
        tables = [self._rate_tables(s) for s in setups]
        bat_rates = [t[0] for t in tables]
        pit_rates = [t[1] for t in tables]
        pools = [s.pitcher_pool for s in setups]
        weights = [
            np.concatenate(([0.0], s.bullpen.weights)) for s in setups
        ]  # the starter cannot be summoned from the bullpen

        score = [np.zeros(n_sims, dtype=np.int32) for _ in range(2)]
        pa_index = [np.zeros(n_sims, dtype=np.int32) for _ in range(2)]
        pitcher = [np.zeros(n_sims, dtype=np.int32) for _ in range(2)]
        pitcher_bf = [np.zeros(n_sims, dtype=np.int32) for _ in range(2)]
        used = [np.zeros((n_sims, len(pools[t])), dtype=bool) for t in range(2)]
        for t in range(2):
            used[t][:, 0] = True  # the starter has been used

        finished = np.zeros(n_sims, dtype=bool)

        for inning in range(1, self.max_innings + 1):
            for half in (AWAY, HOME):
                batting, fielding = half, 1 - half
                playing = ~finished
                if half == HOME and inning >= REGULATION_INNINGS:
                    # No bottom half when the home team is already ahead.
                    playing = playing & ~(score[HOME] > score[AWAY])
                if not playing.any():
                    continue

                ghost = (
                    self.ghost_runner_from_inning is not None
                    and inning >= self.ghost_runner_from_inning
                )
                state = np.where(playing, GHOST_RUNNER_STATE if ghost else 0, TERMINAL_STATE)
                state = state.astype(np.int32)
                active = playing.copy()

                while active.any():
                    idx = np.flatnonzero(active)
                    k = len(idx)

                    slot = pa_index[batting][idx] % LINEUP_SIZE
                    p_idx = pitcher[fielding][idx]
                    raw = (
                        bat_rates[batting][slot]
                        * pit_rates[fielding][p_idx]
                        * state_rates[state[idx]]
                        * side_rates[batting]
                        / lg3
                    ) * env_mult
                    probs = raw / raw.sum(axis=1, keepdims=True)
                    outcome = (probs.cumsum(axis=1) < rng.random((k, 1))).sum(axis=1)
                    outcome = np.minimum(outcome, len(OUTCOMES) - 1)

                    cdf = self._trans_cdf[state[idx], outcome]
                    flat = (cdf < rng.random((k, 1))).sum(axis=1)
                    flat = np.minimum(flat, cdf.shape[1] - 1)
                    next_state = (flat // N_RUN_VALUES).astype(np.int32)
                    runs = (flat % N_RUN_VALUES).astype(np.int32)

                    score[batting][idx] += runs
                    state[idx] = next_state
                    pa_index[batting][idx] += 1

                    # Pitcher accounting, then removal, then replacement.
                    pitcher_bf[fielding][idx] += 1
                    bf = pitcher_bf[fielding][idx]
                    tto = np.minimum((bf - 1) // LINEUP_SIZE + 1, MAX_TTO)
                    bucket = np.searchsorted(BF_BUCKETS, bf)
                    bucket = np.minimum(bucket, len(BF_BUCKETS) - 1)
                    is_starter = (p_idx == 0).astype(np.int32)
                    remove = rng.random(k) < self._hazard[is_starter, tto, bucket]

                    if remove.any():
                        self._replace_pitchers(
                            rng, idx[remove], fielding, pitcher, pitcher_bf, used, weights
                        )

                    active[idx] = next_state != TERMINAL_STATE
                    if half == HOME and inning >= REGULATION_INNINGS:
                        # Walk-off: play stops the instant the home team leads.
                        active &= ~(score[HOME] > score[AWAY])

            if inning >= REGULATION_INNINGS:
                finished |= score[HOME] != score[AWAY]
            if finished.all():
                break

        return GameResult(home_score=score[HOME], away_score=score[AWAY])

    @staticmethod
    def _replace_pitchers(
        rng: np.random.Generator,
        rows: np.ndarray,
        team: int,
        pitcher: list[np.ndarray],
        pitcher_bf: list[np.ndarray],
        used: list[np.ndarray],
        weights: list[np.ndarray],
    ) -> None:
        """Draw a fresh reliever for each simulation in ``rows``.

        Gumbel-max sampling: adding Gumbel noise to log-weights and taking the argmax
        draws from the weight distribution, and it vectorises across simulations with
        different exclusion masks, which ``rng.choice`` cannot do.
        """
        w = np.broadcast_to(weights[team], (len(rows), len(weights[team]))).copy()
        w[used[team][rows]] = 0.0
        exhausted = w.sum(axis=1) <= 0
        if exhausted.any():
            # Rather than fail mid-game, let an exhausted staff repeat an arm.
            w[exhausted] = weights[team]
            w[exhausted, 0] = 0.0

        with np.errstate(divide="ignore"):
            keys = np.log(w) + rng.gumbel(size=w.shape)
        choice = keys.argmax(axis=1)

        pitcher[team][rows] = choice
        pitcher_bf[team][rows] = 0
        used[team][rows, choice] = True
