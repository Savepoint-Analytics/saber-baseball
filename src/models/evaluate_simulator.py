"""Score the plate-appearance simulator as a *predictor*, against the classifiers.

The simulator was validated on what it describes — run scoring, home-field advantage,
starter workloads, score distributions — but never on the thing the classifiers are
judged by. These are two different model families solving the same problem, and until
they are scored on one metric there is no basis for choosing between them.

The comparison needs three pieces of care, all of which this module handles:

* **The same games.** The simulator skips any game missing a lineup or a bullpen, so
  the classifiers are scored on exactly the surviving set, not on the full season.
* **No leakage in the part that matters.** The PA model — the term carrying team
  strength — is fitted on the training seasons only. Structural inputs (the transition
  matrix, the removal policy, weather factors) remain era-pooled and therefore touch
  the test season; they are physical constants rather than strength estimates, so
  this mildly favours the simulator and is noted rather than engineered away.
* **Discrimination separated from calibration.** A model can rank games well and sit
  at the wrong level, or the reverse. AUC answers the first, recentring the second.

Run::

    python -m src.models.evaluate_simulator --test-season 2026 --n-sims 2000
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import typer
from loguru import logger

from src.models.backtest import (
    FORM_MODELS,
    MODELS,
    load_games,
    log_loss,
    walk_forward,
)

app = typer.Typer(add_completion=False, help="Score the simulator against the classifiers.")


def auc(probs: np.ndarray, outcomes: np.ndarray) -> float:
    """Rank-based discrimination, independent of calibration. 0.5 is a coin flip."""
    order = np.argsort(probs)
    ranks = np.empty(len(probs), dtype=float)
    ranks[order] = np.arange(1, len(probs) + 1)
    positives = outcomes == 1
    n1, n0 = positives.sum(), (~positives).sum()
    if n1 == 0 or n0 == 0:
        return float("nan")
    return float((ranks[positives].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def recentred_log_loss(probs: np.ndarray, outcomes: np.ndarray) -> float:
    """Log-loss after shifting log-odds so the mean prediction matches the base rate.

    A diagnostic, not a score: it consumes the test set's own base rate. Its only job
    is to say how much of a model's deficit is a wrong *level* rather than a wrong
    *ranking*.
    """
    base = float(outcomes.mean())
    logit = np.log(np.clip(probs, 1e-9, 1 - 1e-9) / (1 - np.clip(probs, 1e-9, 1 - 1e-9)))
    lo, hi = -3.0, 3.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if float((1 / (1 + np.exp(-(logit + mid)))).mean()) < base:
            lo = mid
        else:
            hi = mid
    return log_loss(1 / (1 + np.exp(-(logit + 0.5 * (lo + hi)))), outcomes)


def simulate_season(
    test_season: int, train_seasons: tuple[int, ...], n_sims: int, seed: int = 17
) -> pd.DataFrame:
    """Win probability per game from the simulator, fitted on ``train_seasons``."""
    import duckdb

    from src.config import settings
    from src.simulation.context import RunEnvironment, load_game_environments
    from src.simulation.form import PitcherFade, PitcherForm
    from src.simulation.game_engine import GameSimulator, TeamSetup
    from src.simulation.lineup import SubstitutionPolicy, load_bullpens, load_lineups
    from src.simulation.pa_model import PAOutcomeModel
    from src.simulation.transitions import TransitionMatrix, load_plate_appearances

    model = PAOutcomeModel().fit(load_plate_appearances(train_seasons))
    transitions = TransitionMatrix.load()
    policy = SubstitutionPolicy.load()
    sim = GameSimulator(
        model,
        transitions,
        policy,
        run_environment=RunEnvironment.calibrate(model, transitions),
        pitcher_form=PitcherForm.calibrate(model, transitions, seasons=train_seasons),
        pitcher_fade=PitcherFade.calibrate(model),
    )

    lineups = load_lineups(test_season)
    bullpens = load_bullpens(test_season)
    environments = load_game_environments(test_season)

    con = duckdb.connect(str((settings.data_dir / "saber.duckdb").resolve()), read_only=True)
    try:
        games = con.sql(
            f"""
            select g.game_pk, g.home_team_id, g.away_team_id, g.home_win,
                   hs.pitcher as home_starter, aws.pitcher as away_starter
            from main_silver.sv_games g
            join main_silver.sv_statcast_pitcher_game hs
              on hs.game_pk = g.game_pk and hs.team_id = g.home_team_id and hs.is_starter
            join main_silver.sv_statcast_pitcher_game aws
              on aws.game_pk = g.game_pk and aws.team_id = g.away_team_id and aws.is_starter
            where g.season = {test_season} and g.season_phase = 'regular'
              and g.home_win is not null
            """
        ).df()
    finally:
        con.close()

    rng = np.random.default_rng(seed)
    rows = []
    for row in games.itertuples():
        home_lineup = lineups.get((row.game_pk, row.home_team_id))
        away_lineup = lineups.get((row.game_pk, row.away_team_id))
        home_pen = bullpens.get(row.home_team_id)
        away_pen = bullpens.get(row.away_team_id)
        if not (home_lineup and away_lineup and home_pen and away_pen):
            continue
        result = sim.simulate(
            TeamSetup(home_lineup, int(row.home_starter), home_pen),
            TeamSetup(away_lineup, int(row.away_starter), away_pen),
            n_sims=n_sims,
            rng=rng,
            run_environment=environments.get(row.game_pk, 1.0),
        )
        rows.append(
            {
                "game_pk": int(row.game_pk),
                "p_sim": result.home_win_probability(),
                "home_win": float(row.home_win),
            }
        )
    logger.info("simulated {} games x {} sims", len(rows), n_sims)
    return pd.DataFrame(rows).sort_values("game_pk")


def compare(sim_probs: pd.DataFrame, test_season: int) -> pd.DataFrame:
    """Score the simulator and every classifier on the identical game set."""
    games = load_games(with_form=True, with_starters=True, with_bullpen=True, with_context=True)
    _, preds = walk_forward(games, models={**MODELS, **FORM_MODELS}, start_season=test_season)
    preds = preds[(preds.season == test_season) & (preds.game_pk.isin(set(sim_probs.game_pk)))]
    outcomes = sim_probs.home_win.to_numpy(dtype=float)

    rows = [("simulator", sim_probs.p_sim.to_numpy(dtype=float))]
    for name in sorted(preds.model.unique()):
        series = preds[preds.model == name].set_index("game_pk").reindex(sim_probs.game_pk)
        rows.append((name, series.p_home.to_numpy(dtype=float)))

    return pd.DataFrame(
        [
            {
                "model": name,
                "log_loss": log_loss(p, outcomes),
                "recentred": recentred_log_loss(p, outcomes),
                "auc": auc(p, outcomes),
                "accuracy": float(((p >= 0.5) == (outcomes == 1)).mean()),
                "sd_p": float(p.std()),
            }
            for name, p in rows
        ]
    ).sort_values("log_loss")


@app.command()
def main(
    test_season: int = typer.Option(2026, "--test-season"),
    n_sims: int = typer.Option(2000, "--n-sims", help="Simulations per game."),
    train_start: int = typer.Option(2023, "--train-start"),
) -> None:
    train = tuple(range(train_start, test_season))
    sim_probs = simulate_season(test_season, train, n_sims)
    table = compare(sim_probs, test_season)
    print(table.round(5).to_string(index=False))


if __name__ == "__main__":
    app()
