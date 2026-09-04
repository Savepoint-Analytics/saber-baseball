"""Season projection — playoff and World Series odds (Phase 4).

Monte-Carlos the remaining schedule to a distribution of final win totals, then seeds
and plays the postseason bracket.

**Driven by any model implementing the ``Baseline`` protocol.** The projector calls
``fit`` on completed games and ``predict_proba`` on the remaining ones, so swapping
Elo for a Bradley-Terry fit, a state-space rating, or the plate-appearance simulator
is a single argument. That is deliberate: comparing model families on *playoff odds*
rather than on game log-loss is the point of building this.

**Why the classifier and not the PA simulator.** Playoff odds are an aggregation of
game outcomes, and scored head to head on 2026 the classifier discriminates better
(AUC 0.5651 against the simulator's 0.5440 — see `docs/methodology.md`). Coin-flipping
a schedule with better per-game probabilities gives better win-total distributions.
The simulator remains the only option for score distributions and player
counterfactuals, which no binary classifier can express.

Run::

    python -m src.simulation.season --season 2026 --n-sims 20000
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb
import numpy as np
import pandas as pd
import typer
from loguru import logger

from src.config import settings
from src.models.baselines import Baseline, EloBaseline

app = typer.Typer(add_completion=False, help="Project a season to playoff odds.")

DIVISION_WINNERS_PER_LEAGUE = 3
WILD_CARDS_PER_LEAGUE = 3
PLAYOFF_TEAMS_PER_LEAGUE = DIVISION_WINNERS_PER_LEAGUE + WILD_CARDS_PER_LEAGUE
# Home-field edge inside a series, in log-odds. Matches the measured regular-season
# rate (~52.8% home) rather than the larger number folklore assumes.
HOME_FIELD_LOGIT = 0.112


def _lake() -> duckdb.DuckDBPyConnection:
    path = (settings.data_dir / "saber.duckdb").resolve()
    if not path.exists():
        raise FileNotFoundError(f"No DuckDB lake at {path}. Run `make dbt-duckdb` first.")
    return duckdb.connect(str(path), read_only=True)


def load_season_state(season: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Standings so far, the games still to play, and the team dimension."""
    con = _lake()
    try:
        standings = con.sql(
            f"""
            with played as (
                select home_team_id as team_id, home_win as won from main_silver.sv_schedule
                where season = {season} and is_played and home_win is not null
                union all
                select away_team_id, 1 - home_win from main_silver.sv_schedule
                where season = {season} and is_played and home_win is not null
            )
            select team_id, count(*) as games, sum(won) as wins
            from played group by 1
            """
        ).df()
        remaining = con.sql(
            f"""
            select game_pk, game_date, season, home_team_id, away_team_id, venue_id
            from main_silver.sv_schedule
            where season = {season} and is_remaining
            order by game_date, game_pk
            """
        ).df()
        teams = con.sql(
            f"select team_id, team_name, league_id, division_id from main_silver.sv_teams "
            f"where season = {season}"
        ).df()
    finally:
        con.close()
    return standings, remaining, teams


def load_training_games(season: int, history: int = 3) -> pd.DataFrame:
    """Completed games a model fits on: this season plus prior ones for context."""
    con = _lake()
    try:
        return con.sql(
            f"""
            select game_pk, game_date, season, season_phase,
                   home_team_id, away_team_id, home_score, away_score, home_win
            from main_silver.sv_games
            where season between {season - history} and {season}
              and season_phase = 'regular' and home_win is not null
            order by game_date, game_pk
            """
        ).df()
    finally:
        con.close()


def _play_series(
    strength: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    length: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """Winner of a best-of-``length`` series; returns the winning team index per sim.

    Strength is a log-odds rating, so a matchup probability is a logistic difference —
    the log5 formulation. The higher seed holds home field, applied as a constant
    edge on the majority of games rather than by modelling the 2-3-2 pattern
    explicitly; across a series the difference between the two is negligible and the
    pattern has changed format several times in the era being simulated.
    """
    need = length // 2 + 1
    # strength is (n_sims, n_teams) and high/low are per-simulation column indices,
    # so both axes must be indexed together.
    rows = np.arange(strength.shape[0])
    p = 1.0 / (1.0 + np.exp(-((strength[rows, high] - strength[rows, low]) + HOME_FIELD_LOGIT)))
    wins_high = np.zeros(len(high), dtype=np.int16)
    wins_low = np.zeros(len(high), dtype=np.int16)
    for _ in range(length):
        live = (wins_high < need) & (wins_low < need)
        if not live.any():
            break
        won = rng.random(len(high)) < p
        wins_high += (live & won).astype(np.int16)
        wins_low += (live & ~won).astype(np.int16)
    return np.where(wins_high >= need, high, low)


@dataclass
class SeasonProjection:
    """Simulated outcomes for one season."""

    teams: np.ndarray
    team_names: dict[int, str]
    win_totals: np.ndarray  # (n_sims, n_teams)
    made_playoffs: np.ndarray
    won_division: np.ndarray
    won_pennant: np.ndarray
    won_world_series: np.ndarray

    def odds(self) -> pd.DataFrame:
        return (
            pd.DataFrame(
                {
                    "team_id": self.teams,
                    "team": [self.team_names.get(int(t), str(t)) for t in self.teams],
                    "proj_wins": self.win_totals.mean(axis=0),
                    "win_p10": np.percentile(self.win_totals, 10, axis=0),
                    "win_p90": np.percentile(self.win_totals, 90, axis=0),
                    "division_pct": 100 * self.won_division.mean(axis=0),
                    "playoff_pct": 100 * self.made_playoffs.mean(axis=0),
                    "pennant_pct": 100 * self.won_pennant.mean(axis=0),
                    "ws_pct": 100 * self.won_world_series.mean(axis=0),
                }
            )
            .sort_values("playoff_pct", ascending=False)
            .reset_index(drop=True)
        )


def project_season(
    season: int,
    model: Baseline | None = None,
    n_sims: int = 10000,
    rng: np.random.Generator | None = None,
) -> SeasonProjection:
    """Roll the remaining schedule and postseason ``n_sims`` times."""
    rng = rng or np.random.default_rng()
    model = model or EloBaseline()

    standings, remaining, teams = load_season_state(season)
    training = load_training_games(season)
    model.fit(training)

    team_ids = np.sort(teams.team_id.to_numpy(dtype=int))
    index = {int(t): i for i, t in enumerate(team_ids)}
    names = {int(r.team_id): str(r.team_name) for r in teams.itertuples()}
    league = np.array([int(teams.set_index("team_id").league_id[t]) for t in team_ids])
    division = np.array([int(teams.set_index("team_id").division_id[t]) for t in team_ids])

    wins = np.zeros((n_sims, len(team_ids)))
    for r in standings.itertuples():
        if int(r.team_id) in index:
            wins[:, index[int(r.team_id)]] = float(r.wins)

    if len(remaining):
        # Models expect the outcome columns to exist even when unknown; an unplayed
        # game legitimately has no score, and asserting the schema here is better than
        # a KeyError inside whichever model family is being trialled.
        for col in ("home_score", "away_score", "home_win"):
            if col not in remaining.columns:
                remaining[col] = np.nan
        if "season_phase" not in remaining.columns:
            remaining["season_phase"] = "regular"

        p_home = model.predict_proba(remaining)
        outcomes = rng.random((n_sims, len(remaining))) < p_home[None, :]

        # Accumulate via one-hot incidence rather than scatter-add: a team appears in
        # many remaining games, so the indices repeat and plain fancy indexing would
        # silently keep only the last write.
        home_hot = np.zeros((len(remaining), len(team_ids)))
        away_hot = np.zeros((len(remaining), len(team_ids)))
        for g, (h, a) in enumerate(
            zip(remaining.home_team_id, remaining.away_team_id, strict=True)
        ):
            home_hot[g, index[int(h)]] = 1.0
            away_hot[g, index[int(a)]] = 1.0
        wins += outcomes.astype(float) @ home_hot + (~outcomes).astype(float) @ away_hot
        logger.info("projected {} remaining games over {} sims", len(remaining), n_sims)
    else:
        logger.warning("season {} has no remaining games; projecting the final standings", season)

    # Tiny random jitter breaks ties. MLB's real tiebreakers are head-to-head first,
    # which would need the full head-to-head matrix; exact ties are rare enough that
    # the difference is far below Monte-Carlo noise, and pretending otherwise would
    # be false precision.
    keys = wins + rng.random(wins.shape) * 1e-6

    made = np.zeros_like(wins, dtype=bool)
    div_winner = np.zeros_like(wins, dtype=bool)
    pennant = np.zeros_like(wins, dtype=bool)
    champion = np.zeros_like(wins, dtype=bool)

    # Strength for postseason series: projected win rate on a log-odds scale.
    rate = np.clip(
        wins / np.maximum(wins.sum(axis=1, keepdims=True) / len(team_ids) * 2, 1), 0.05, 0.95
    )
    strength = np.log(rate / (1 - rate))

    league_winners = {}
    for lg in np.unique(league):
        lg_cols = np.flatnonzero(league == lg)
        seeds = np.empty((n_sims, PLAYOFF_TEAMS_PER_LEAGUE), dtype=int)

        # Division winners, then the best remaining clubs as wild cards.
        taken = np.zeros((n_sims, len(team_ids)), dtype=bool)
        div_list = np.unique(division[lg_cols])
        winners = []
        for d in div_list:
            cols = np.flatnonzero((league == lg) & (division == d))
            best = cols[np.argmax(keys[:, cols], axis=1)]
            winners.append(best)
            taken[np.arange(n_sims), best] = True
            div_winner[np.arange(n_sims), best] = True
        winners_arr = np.stack(winners, axis=1)
        order = np.argsort(-np.take_along_axis(keys, winners_arr, axis=1), axis=1)
        seeds[:, :3] = np.take_along_axis(winners_arr, order, axis=1)

        masked = np.where(taken[:, lg_cols], -np.inf, keys[:, lg_cols])
        wc_order = np.argsort(-masked, axis=1)[:, :WILD_CARDS_PER_LEAGUE]
        seeds[:, 3:] = lg_cols[wc_order]

        made[np.arange(n_sims)[:, None], seeds] = True

        # Wild Card round: 3v6 and 4v5, best-of-three, top two seeds on bye.
        w36 = _play_series(strength, seeds[:, 2], seeds[:, 5], 3, rng)
        w45 = _play_series(strength, seeds[:, 3], seeds[:, 4], 3, rng)
        # Division Series: the top seed draws the lower surviving seed.
        low_key = np.take_along_axis(keys, w36[:, None], 1)[:, 0]
        oth_key = np.take_along_axis(keys, w45[:, None], 1)[:, 0]
        lower = np.where(low_key < oth_key, w36, w45)
        higher = np.where(low_key < oth_key, w45, w36)
        d1 = _play_series(strength, seeds[:, 0], lower, 5, rng)
        d2 = _play_series(strength, seeds[:, 1], higher, 5, rng)
        champ = _play_series(strength, d1, d2, 7, rng)
        pennant[np.arange(n_sims), champ] = True
        league_winners[int(lg)] = champ

    lg_ids = sorted(league_winners)
    a, b = league_winners[lg_ids[0]], league_winners[lg_ids[1]]
    ws = _play_series(strength, a, b, 7, rng)
    champion[np.arange(n_sims), ws] = True

    return SeasonProjection(
        teams=team_ids,
        team_names=names,
        win_totals=wins,
        made_playoffs=made,
        won_division=div_winner,
        won_pennant=pennant,
        won_world_series=champion,
    )


@app.command()
def main(
    season: int = typer.Option(2026, "--season"),
    n_sims: int = typer.Option(10000, "--n-sims"),
) -> None:
    proj = project_season(season, n_sims=n_sims, rng=np.random.default_rng(7))
    print(proj.odds().round(1).to_string(index=False))


if __name__ == "__main__":
    app()
