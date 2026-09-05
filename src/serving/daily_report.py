"""Today's predictions, ready to publish.

Produces the three things this project can currently stand behind:

* **Win probability** for every game on today's slate, from the best-scoring model
  (Elo blended with starting-pitcher form, 0.6769 log-loss walk-forward).
* **A projected score line**, from the negative-binomial run model.
* **Playoff and World Series odds** from the season projection.

**Why the probable pitcher matters here.** Every backtest in this project keyed
starter form off who *actually* started, which is available after the fact and not
before. A live slate has no Statcast rows at all, so form is looked up from each
announced probable's most recent start. That is the substitution flagged throughout
the methodology, made real — and it is why `sv_schedule` carries the probables.

Every output carries the MLBAM attribution required of anything built on Statcast
and the Stats API (``CLAUDE.md`` §5).

Run::

    python -m src.serving.daily_report
    python -m src.serving.daily_report --date 2026-09-05 --no-odds
"""

from __future__ import annotations

from datetime import date

import duckdb
import numpy as np
import pandas as pd
import typer
from loguru import logger

from src.compliance.attributions import Source, attribution_block
from src.config import settings
from src.models.backtest import STARTER_COLUMNS, load_games
from src.models.score_models import NegativeBinomialScoreModel
from src.models.statcast import StarterOnlyLogistic

app = typer.Typer(add_completion=False, help="Publishable predictions for today.")

ATTRIBUTION_SOURCES = [Source.MLB_STATS_API, Source.STATCAST]


def _lake() -> duckdb.DuckDBPyConnection:
    path = (settings.data_dir / "saber.duckdb").resolve()
    if not path.exists():
        raise FileNotFoundError(f"No DuckDB lake at {path}. Run `make dbt-duckdb` first.")
    return duckdb.connect(str(path), read_only=True)


def load_slate(day: date) -> pd.DataFrame:
    """Games scheduled for ``day``, with announced starters and conditions."""
    con = _lake()
    try:
        return con.sql(
            f"""
            select game_pk, game_date, season, home_team_id, home_team,
                   away_team_id, away_team, venue_id,
                   home_probable_pitcher_id, away_probable_pitcher_id,
                   weather_condition, weather_temp_f
            from main_silver.sv_schedule
            where game_date = date '{day.isoformat()}'
            order by game_pk
            """
        ).df()
    finally:
        con.close()


def latest_starter_form(season: int) -> pd.DataFrame:
    """Each pitcher's most recent start, as his current form."""
    con = _lake()
    try:
        return con.sql(
            f"""
            select * exclude (rn) from (
                select f.*, row_number() over (
                    partition by pitcher order by game_date desc, game_pk desc
                ) as rn
                from main_gold.gd_starter_form f
                where season = {season}
            ) where rn = 1
            """
        ).df()
    finally:
        con.close()


def build_features(slate: pd.DataFrame, form: pd.DataFrame) -> pd.DataFrame:
    """Attach each probable starter's current form to the slate.

    A starter with no prior start this season (a debut, or a September call-up) simply
    has no form; the columns stay null and the model's own null handling treats that
    as "no information beyond the team rating" rather than inventing a value.
    """
    lookup = form.set_index("pitcher")
    games = slate.copy()
    games["home_score"] = np.nan
    games["away_score"] = np.nan
    games["home_win"] = np.nan
    games["season_phase"] = "regular"

    available = [c for c in STARTER_COLUMNS if c in lookup.columns]
    for side, col in (("home", "home_probable_pitcher_id"), ("away", "away_probable_pitcher_id")):
        # Reindex rather than looking up row by row: it is vectorised, and a pitcher
        # who is absent yields NaN by construction instead of needing a guard.
        matched = lookup.reindex(games[col].astype("Int64"))
        for feature in STARTER_COLUMNS:
            games[f"{side}_{feature}"] = (
                matched[feature].to_numpy(dtype=float) if feature in available else np.nan
            )
    return games


def predict_slate(day: date) -> pd.DataFrame:
    """Win probability and a projected score line for every game on ``day``."""
    slate = load_slate(day)
    if slate.empty:
        return slate

    season = int(slate.season.iloc[0])
    history = load_games(with_form=True, with_starters=True)
    history = history[history.season <= season]

    win_model = StarterOnlyLogistic().fit(history)
    score_model = NegativeBinomialScoreModel().fit(history)

    features = build_features(slate, latest_starter_form(season))
    p_home = win_model.predict_proba(features)
    mu_home, mu_away = score_model._means(features)

    out = slate[["game_pk", "away_team", "home_team", "weather_condition", "weather_temp_f"]].copy()
    out["home_win_pct"] = 100 * p_home
    out["proj_away"] = mu_away
    out["proj_home"] = mu_home
    known = (
        features["home_sp_form_xwoba_allowed"].notna()
        & features["away_sp_form_xwoba_allowed"].notna()
    )
    out["starters_known"] = known.to_numpy()
    return out.sort_values("home_win_pct", ascending=False)


@app.command()
def main(
    day: str = typer.Option(None, "--date", help="YYYY-MM-DD; defaults to today."),
    odds: bool = typer.Option(True, "--odds/--no-odds", help="Include playoff odds."),
    n_sims: int = typer.Option(20000, "--n-sims"),
) -> None:
    target = date.fromisoformat(day) if day else date.today()
    games = predict_slate(target)

    print(f"\nSAVEPOINT ANALYTICS — MLB PREDICTIONS FOR {target.isoformat()}\n")
    if games.empty:
        print("  No games scheduled.")
    else:
        print(f"{'matchup':<46}{'home win':>10}{'proj score':>14}{'conditions':>22}")
        for r in games.itertuples():
            matchup = f"{r.away_team} @ {r.home_team}"
            score = f"{r.proj_away:.1f} - {r.proj_home:.1f}"
            cond = f"{r.weather_condition or '?'}, {r.weather_temp_f or '?'}F"
            flag = "" if r.starters_known else "  (starter form unavailable)"
            print(f"{matchup:<46}{r.home_win_pct:>9.1f}%{score:>14}{cond:>22}{flag}")

    if odds and not games.empty:
        from src.simulation.season import project_season

        season = int(load_slate(target).season.iloc[0])
        proj = project_season(season, n_sims=n_sims, rng=np.random.default_rng(7))
        table = proj.odds()
        table = table[table.playoff_pct > 0.5]
        print(f"\n\nPLAYOFF ODDS — {season} (teams above 0.5%)")
        # Measured on 2023-25 from 1 September (docs/methodology.md, "Playoff odds,
        # calibration-tested"): clubs stated at 75-95% qualified half the time. The
        # caveat travels with the number until rating uncertainty is propagated.
        print(
            "Calibration note: backtested odds are overconfident in the 25-95% range "
            "(slope 0.58); read 85% as roughly 70%.\n"
        )
        print(
            table[["team", "proj_wins", "division_pct", "playoff_pct", "ws_pct"]]
            .round(1)
            .to_string(index=False)
        )

    print("\n" + attribution_block(ATTRIBUTION_SOURCES))
    logger.debug("report complete for {}", target)


if __name__ == "__main__":
    app()
