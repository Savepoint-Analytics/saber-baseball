"""Calibration backtest for playoff odds (Phase 4).

Every validation in this project before this module was at *game* level. Playoff
odds are a different claim — "this club makes the field 70% of the time" — and a
per-game log-loss says nothing about whether the aggregation from games to seeds to
a bracket preserves calibration. So: rewind each finished season to a date, project
it exactly as ``make today`` would have, and score the stated percentages against
what happened.

Only seasons played under the current 12-team format (2022 onward) are comparable;
the bracket in :mod:`src.simulation.season` is that format and would mis-seed the
10-team years.

Sample size is the honest limit. Three seasons is 90 team-seasons, which resolves
"is it wildly off" but not "is it off by five points" — see the reliability table's
bin counts before reading the gaps as signal.

Run::

    python -m src.models.backtest_season --seasons 2023 --seasons 2024 --seasons 2025
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable

import duckdb
import numpy as np
import pandas as pd
import typer
from loguru import logger

from src.config import settings
from src.models.backtest import brier_score, log_loss
from src.models.baselines import Baseline, EloBaseline
from src.simulation.season import project_season

app = typer.Typer(add_completion=False, help="Score historical playoff odds for calibration.")

# The four claims the projection makes, with the base rate a know-nothing forecast
# would state (12 of 30 qualify, 6 win divisions, 2 win pennants, 1 wins it all).
# Keyed by the claim; values are (odds column, outcome column, base rate).
OUTCOMES: dict[str, tuple[str, str, float]] = {
    "division": ("division_pct", "won_division", 6 / 30),
    "playoffs": ("playoff_pct", "made_playoffs", 12 / 30),
    "pennant": ("pennant_pct", "won_pennant", 2 / 30),
    "world_series": ("ws_pct", "won_world_series", 1 / 30),
}
# Coarse bins: with ~90 team-seasons, ten-percent bins would hold single digits.
RELIABILITY_EDGES = np.array([0.0, 0.05, 0.25, 0.5, 0.75, 0.95, 1.0])


def _lake() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str((settings.data_dir / "saber.duckdb").resolve()), read_only=True)


def outcomes_from_games(games: pd.DataFrame, teams: pd.DataFrame) -> pd.DataFrame:
    """What actually happened, one row per team.

    ``games`` is a season's completed games (regular and postseason) with
    ``season_phase`` and ``game_type``; ``teams`` the team dimension with divisions.
    The postseason record is the ground truth for qualification, pennants and the
    title: any club that appears in October was in the field, the two in the World
    Series won pennants, and the one that won the last game won it all. Division
    winners come from final regular-season standings with MLB's first tiebreaker,
    head-to-head record, applied — it has decided a title three times in the 12-team
    era (2022 Braves/Mets, 2023 Astros/Rangers, 2025 Blue Jays/Yankees) and it is
    the one that decides the seeding a projection is scored against. Deeper
    tiebreakers (intra-division record) have not been needed and are not modelled;
    the function logs if it ever finds a tie they would have to break.
    """
    regular = games[games.season_phase == "regular"]
    post = games[games.season_phase == "postseason"]

    wins = pd.concat(
        [
            regular[["home_team_id", "home_win"]].rename(
                columns={"home_team_id": "team_id", "home_win": "won"}
            ),
            regular[["away_team_id", "home_win"]]
            .assign(won=lambda d: 1 - d.home_win)
            .rename(columns={"away_team_id": "team_id"})[["team_id", "won"]],
        ]
    )
    final = wins.groupby("team_id").won.sum().rename("actual_wins")
    out = teams[["team_id", "league_id", "division_id"]].merge(final, on="team_id", how="left")

    in_october = set(post.home_team_id) | set(post.away_team_id)
    ws = post[post.game_type == "W"]
    in_ws = set(ws.home_team_id) | set(ws.away_team_id)
    ws_wins = pd.concat(
        [
            ws.loc[ws.home_win == 1, "home_team_id"],
            ws.loc[ws.home_win == 0, "away_team_id"],
        ]
    ).value_counts()
    champion = {int(ws_wins.idxmax())} if len(ws_wins) else set()

    out["made_playoffs"] = out.team_id.isin(in_october)
    out["won_pennant"] = out.team_id.isin(in_ws)
    out["won_world_series"] = out.team_id.isin(champion)

    top = out.groupby(["league_id", "division_id"]).actual_wins.transform("max")
    out["won_division"] = out.actual_wins == top
    for (_lg, _dv), grp in out[out.won_division].groupby(["league_id", "division_id"]):
        if len(grp) > 1:
            tied = _break_tie_head_to_head(regular, [int(t) for t in grp.team_id])
            out.loc[out.team_id.isin(grp.team_id) & (out.team_id != tied), "won_division"] = False
    if out.won_division.sum() != 6:
        logger.warning(
            "{} division winners after head-to-head — a deeper MLB tiebreaker applied",
            int(out.won_division.sum()),
        )
    return out.drop(columns=["league_id", "division_id"])


def _break_tie_head_to_head(regular: pd.DataFrame, tied: list[int]) -> int:
    """Best head-to-head record among ``tied`` clubs, MLB's first tiebreaker."""
    h2h = regular[regular.home_team_id.isin(tied) & regular.away_team_id.isin(tied)]
    record = pd.concat(
        [
            h2h.loc[h2h.home_win == 1, "home_team_id"],
            h2h.loc[h2h.home_win == 0, "away_team_id"],
        ]
    ).value_counts()
    winner = int(record.idxmax())
    logger.info("division tie among {} broken head-to-head for {}", tied, winner)
    return winner


def load_actual_outcomes(season: int) -> pd.DataFrame:
    con = _lake()
    try:
        games = con.sql(
            f"""
            select season_phase, game_type, home_team_id, away_team_id, home_win
            from main_silver.sv_games
            where season = {season} and home_win is not null
            """
        ).df()
        teams = con.sql(
            f"select team_id, league_id, division_id from main_silver.sv_teams "
            f"where season = {season}"
        ).df()
    finally:
        con.close()
    return outcomes_from_games(games, teams)


def backtest(
    seasons: list[int],
    as_of: tuple[int, int] = (9, 1),
    n_sims: int = 5000,
    model_factory: Callable[[], Baseline] = EloBaseline,
    seed: int = 7,
) -> pd.DataFrame:
    """Project each season from ``as_of`` (month, day) and join what happened.

    Returns one row per team-season with the stated percentages and the outcomes.
    A fresh model per season: the projector fits on history up to the cut, and a
    model carrying state across calls would let one season's outcome into the next
    season's forecast.
    """
    frames = []
    for season in seasons:
        cut = dt.date(season, *as_of)
        proj = project_season(
            season,
            model=model_factory(),
            n_sims=n_sims,
            rng=np.random.default_rng(seed + season),
            as_of=cut,
        )
        odds = proj.odds().assign(season=season, as_of=cut)
        actual = load_actual_outcomes(season)
        frames.append(odds.merge(actual, on="team_id", how="left"))
        logger.info("season {} projected from {}: {} rows", season, cut, len(odds))
    return pd.concat(frames, ignore_index=True)


def calibration_slope(probs: np.ndarray, outcomes: np.ndarray) -> tuple[float, float]:
    """Slope of outcome on logit(p) — 1 is calibrated, below 1 is overconfident.

    Reliability bins say *where* a forecast is off; this says *how* in one number a
    recalibration could act on. Returned with its standard error, because on ninety
    team-seasons the error is the part that matters.
    """
    import statsmodels.api as sm

    p = np.clip(np.asarray(probs, dtype=float), 1e-3, 1 - 1e-3)
    z = np.log(p / (1 - p))
    y = np.asarray(outcomes, dtype=float)
    if y.sum() in (0, len(y)):
        return float("nan"), float("nan")
    fit = sm.Logit(y, sm.add_constant(z)).fit(disp=0)
    return float(fit.params[1]), float(fit.bse[1])


def calibration_summary(results: pd.DataFrame) -> pd.DataFrame:
    """Brier, log-loss and calibration slope per claim, against the base rate.

    ``skill`` is 1 - Brier/Brier_base: positive means the projection beat stating
    the base rate for every club, which is the minimum bar for publishing a number.
    """
    rows = []
    for name, (key, col, base) in OUTCOMES.items():
        p = results[key].to_numpy() / 100
        y = results[col].to_numpy(dtype=float)
        b = brier_score(p, y)
        b0 = brier_score(np.full_like(p, base), y)
        slope, se = calibration_slope(p, y)
        rows.append(
            {
                "claim": name,
                "n": len(y),
                "events": int(y.sum()),
                "sum_predicted": float(p.sum()),
                "brier": b,
                "brier_base": b0,
                "skill": 1 - b / b0,
                "log_loss": log_loss(p, y),
                "slope": slope,
                "slope_se": se,
            }
        )
    return pd.DataFrame(rows)


def reliability(results: pd.DataFrame, claim: str = "playoffs") -> pd.DataFrame:
    """Reliability table for one claim on the coarse bins."""
    key, col, _ = OUTCOMES[claim]
    p = results[key].to_numpy() / 100
    y = results[col].to_numpy(dtype=float)
    idx = np.clip(np.digitize(p, RELIABILITY_EDGES, right=True) - 1, 0, len(RELIABILITY_EDGES) - 2)
    rows = []
    for b in range(len(RELIABILITY_EDGES) - 1):
        mask = idx == b
        if not mask.any():
            continue
        rows.append(
            {
                "bin": f"{RELIABILITY_EDGES[b]:.0%}-{RELIABILITY_EDGES[b + 1]:.0%}",
                "n": int(mask.sum()),
                "mean_predicted": float(p[mask].mean()),
                "observed": float(y[mask].mean()),
                "hits": int(y[mask].sum()),
            }
        )
    return pd.DataFrame(rows)


def win_total_summary(results: pd.DataFrame) -> dict[str, float]:
    """How the win-total projections did: error and 80% interval coverage."""
    err = results.proj_wins - results.actual_wins
    covered = (results.actual_wins >= results.win_p10) & (results.actual_wins <= results.win_p90)
    return {
        "mae": float(err.abs().mean()),
        "rmse": float(np.sqrt((err**2).mean())),
        "bias": float(err.mean()),
        "p10_p90_coverage": float(covered.mean()),
        "mean_interval_width": float((results.win_p90 - results.win_p10).mean()),
    }


@app.command()
def main(
    seasons: list[int] = typer.Option([2023, 2024, 2025], "--seasons"),
    n_sims: int = typer.Option(5000, "--n-sims"),
    month: int = typer.Option(9, "--month"),
    day: int = typer.Option(1, "--day"),
) -> None:
    results = backtest(seasons, as_of=(month, day), n_sims=n_sims)

    print(f"\nPLAYOFF ODDS CALIBRATION — projected from {month}/{day}, seasons {seasons}\n")
    print(calibration_summary(results).round(4).to_string(index=False))
    for claim in OUTCOMES:
        print(f"\nReliability — {claim}")
        print(reliability(results, claim).round(3).to_string(index=False))
    print("\nWin totals")
    for k, v in win_total_summary(results).items():
        print(f"  {k:<22}{v:.3f}")

    # The misses worth looking at: confident calls that went the other way.
    results["playoff_miss"] = (results.playoff_pct / 100 - results.made_playoffs).abs()
    worst = results.sort_values("playoff_miss", ascending=False).head(8)
    print("\nLargest playoff-odds misses")
    print(
        worst[["season", "team", "proj_wins", "actual_wins", "playoff_pct", "made_playoffs"]]
        .round(1)
        .to_string(index=False)
    )


if __name__ == "__main__":
    app()
