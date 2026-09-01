"""Walk-forward backtest + calibration harness for game-outcome models.

``docs/methodology.md`` sets the evaluation contract: **log-loss** against an
Elo/home-field baseline, plus **calibration** (reliability curve, Brier score) on
held-out seasons. This module implements it once so every model — the baselines
today, gradient-boosted and Bayesian models later — is judged the same way.

Walk-forward, not a random split: a random train/test split over games would leak
the future into the past (a September game training a model that predicts April) and
flatter every model. For each test season we refit on *only* the seasons before it.

Run::

    python -m src.models.backtest                    # all baselines, 2017..latest
    python -m src.models.backtest --start-season 2022
"""

from __future__ import annotations

from collections.abc import Callable

import duckdb
import numpy as np
import pandas as pd
import typer
from loguru import logger

from src.config import settings
from src.models.baselines import (
    Baseline,
    EloBaseline,
    HomeFieldBaseline,
    Log5Baseline,
    PythagoreanBaseline,
)

app = typer.Typer(add_completion=False, help="Backtest game-outcome models.")

_EPS = 1e-15

# Factories, not instances: each test season must start from a clean model, and Elo
# in particular carries mutable state that would leak across folds if reused.
MODELS: dict[str, Callable[[], Baseline]] = {
    "home_field": HomeFieldBaseline,
    "log5_winpct": Log5Baseline,
    "pythagorean": PythagoreanBaseline,
    "elo": EloBaseline,
}


def load_games(regular_only: bool = True) -> pd.DataFrame:
    """Load the game spine from the DuckDB lake.

    Regular season only by default: the postseason is a biased sample (only good
    teams, short series, unrepresentative bullpen usage), so fitting or scoring on it
    would distort both the ratings and the metrics — the same reasoning that split
    ``season_phase`` in ``gd_team_season_record``.
    """
    path = (settings.data_dir / "saber.duckdb").resolve()
    if not path.exists():
        raise FileNotFoundError(f"No DuckDB lake at {path}. Run `make dbt-duckdb` first.")

    where = "where home_win is not null"
    if regular_only:
        where += " and season_phase = 'regular'"
    con = duckdb.connect(str(path), read_only=True)
    try:
        return con.sql(
            f"""
            select game_pk, game_date, season, season_phase,
                   home_team_id, away_team_id, home_score, away_score, home_win
            from main_silver.sv_games
            {where}
            order by game_date, game_pk
            """
        ).df()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def log_loss(probs: np.ndarray, outcomes: np.ndarray) -> float:
    """Mean binary log-loss — the primary metric (lower is better).

    Probabilities are clipped away from 0 and 1: a single confident miss would
    otherwise return infinity and destroy the average.
    """
    p = np.clip(np.asarray(probs, dtype=float), _EPS, 1 - _EPS)
    y = np.asarray(outcomes, dtype=float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def brier_score(probs: np.ndarray, outcomes: np.ndarray) -> float:
    """Mean squared error of the probabilities (lower is better)."""
    return float(np.mean((np.asarray(probs, dtype=float) - np.asarray(outcomes, dtype=float)) ** 2))


def accuracy(probs: np.ndarray, outcomes: np.ndarray) -> float:
    """Share of games where the favourite won. Reported for intuition only."""
    return float(np.mean((np.asarray(probs) >= 0.5) == (np.asarray(outcomes) == 1)))


def calibration_table(probs: np.ndarray, outcomes: np.ndarray, bins: int = 10) -> pd.DataFrame:
    """Reliability curve: predicted vs. observed win rate per probability bucket.

    A well-calibrated model sits on the diagonal — when it says 60%, the home team
    wins about 60% of the time. Log-loss alone cannot show this, which is why
    ``docs/methodology.md`` requires both.
    """
    p = np.asarray(probs, dtype=float)
    y = np.asarray(outcomes, dtype=float)
    edges = np.linspace(0.0, 1.0, bins + 1)
    idx = np.clip(np.digitize(p, edges, right=True) - 1, 0, bins - 1)

    rows = []
    for b in range(bins):
        mask = idx == b
        if not mask.any():
            continue
        rows.append(
            {
                "bin": f"[{edges[b]:.1f}, {edges[b + 1]:.1f})",
                "n": int(mask.sum()),
                "mean_predicted": float(p[mask].mean()),
                "observed": float(y[mask].mean()),
                "gap": float(p[mask].mean() - y[mask].mean()),
            }
        )
    return pd.DataFrame(rows)


def expected_calibration_error(probs: np.ndarray, outcomes: np.ndarray, bins: int = 10) -> float:
    """Sample-weighted mean |predicted - observed| across reliability bins."""
    table = calibration_table(probs, outcomes, bins)
    if table.empty:
        return float("nan")
    return float(np.average(table["gap"].abs(), weights=table["n"]))


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------
def walk_forward(
    games: pd.DataFrame,
    models: dict[str, Callable[[], Baseline]] | None = None,
    start_season: int | None = None,
    min_train_seasons: int = 2,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Refit each model on all prior seasons, score it on each held-out season.

    Returns ``(per_season_metrics, predictions)``. Keeping the raw predictions is
    what makes the calibration curves and any later error analysis possible without
    re-running the whole backtest.
    """
    models = models or MODELS
    seasons = sorted(games["season"].unique())
    testable = [s for s in seasons if s >= seasons[min_train_seasons]]
    if start_season is not None:
        testable = [s for s in testable if s >= start_season]
    if not testable:
        raise ValueError("No testable seasons — need more history than min_train_seasons.")

    metric_rows: list[dict[str, object]] = []
    pred_frames: list[pd.DataFrame] = []

    for season in testable:
        train = games[games["season"] < season]
        test = games[games["season"] == season]
        logger.info("season {}: train={} games, test={} games", season, len(train), len(test))

        for name, factory in models.items():
            model = factory().fit(train)
            probs = model.predict_proba(test)
            y = test["home_win"].to_numpy(dtype=float)

            metric_rows.append(
                {
                    "season": int(season),
                    "model": name,
                    "n": len(test),
                    "log_loss": log_loss(probs, y),
                    "brier": brier_score(probs, y),
                    "accuracy": accuracy(probs, y),
                    "ece": expected_calibration_error(probs, y),
                }
            )
            pred_frames.append(
                test[["game_pk", "season", "game_date"]].assign(
                    model=name, p_home=probs, home_win=y
                )
            )

    return pd.DataFrame(metric_rows), pd.concat(pred_frames, ignore_index=True)


def summarize(
    metrics: pd.DataFrame, exclude_seasons: tuple[int, ...] = (2020, 2021)
) -> pd.DataFrame:
    """Mean metrics per model across seasons.

    2020 (60 games) and 2021 (trained on that 60-game season) are excluded by
    default: both folds are distorted by the shortened schedule, and leaving them in
    would penalise every model for a pandemic rather than for its own errors. They
    remain in the per-season table.
    """
    kept = metrics[~metrics["season"].isin(exclude_seasons)]
    out = (
        kept.groupby("model")
        .agg(
            seasons=("season", "nunique"),
            games=("n", "sum"),
            log_loss=("log_loss", "mean"),
            brier=("brier", "mean"),
            accuracy=("accuracy", "mean"),
            ece=("ece", "mean"),
        )
        .sort_values("log_loss")
    )
    return out.reset_index()


@app.command()
def main(
    start_season: int = typer.Option(None, "--start-season", help="First season to score."),
    include_postseason: bool = typer.Option(False, "--include-postseason"),
    bins: int = typer.Option(10, "--bins", help="Reliability-curve buckets."),
) -> None:
    games = load_games(regular_only=not include_postseason)
    logger.info(
        "loaded {} games, seasons {}-{}", len(games), games.season.min(), games.season.max()
    )

    metrics, preds = walk_forward(games, start_season=start_season)

    print("\n=== Per-season log-loss (lower is better) ===")
    wide = metrics.pivot_table(index="season", columns="model", values="log_loss")
    print(wide.round(4).to_string())

    print("\n=== Summary (2020/21 folds excluded) ===")
    print(summarize(metrics).round(4).to_string(index=False))

    best = summarize(metrics).iloc[0]["model"]
    sub = preds[preds.model == best]
    print(f"\n=== Reliability curve — {best} ===")
    print(
        calibration_table(sub.p_home.to_numpy(), sub.home_win.to_numpy(), bins)
        .round(4)
        .to_string(index=False)
    )


if __name__ == "__main__":
    app()
