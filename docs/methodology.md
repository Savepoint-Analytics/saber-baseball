# Methodology

How saber-baseball turns raw sources into trustworthy, reproducible analysis. This is
the living spec behind the roadmap in `README.md` and the modeling plan in
`PROJECT_CONTEXT.md` §7.

## Reproducibility contract

Every reported number must trace **raw source → transform → output** and re-run from a
clean checkout (`PROJECT_CONTEXT.md` §5). Concretely:

- Raw pulls land immutably in the date-partitioned **bronze** layer (Parquet in MinIO
  or `./data/bronze`); they are never edited in place.
- Transforms are **dbt** models (bronze → silver → gold), tested and documented.
- Models are tracked in **MLflow** (params, metrics, calibration), so any figure in a
  report points back to a run id and the commit that produced it.
- Data entering silver is validated by **Pandera** schemas (`src/schemas/`).

## Data flow

```
sources → src/ingestion (rate-limited, cached) → bronze Parquet
        → MinIO s3://sabermetrics/baseball/ (Savepoint-Lab)
        → dbt (duckdb) bronze views → silver → gold marts, all in DuckDB
        → src/warehouse/load_postgres → silver + gold copied into Postgres (baseball.*)
        → src/features, src/models, src/simulation → MLflow + serving
```

The ingest → dbt → publish leg of that chain is sequenced by **Prefect**
(`orchestration/flows.py`, daily at 09:00 ET). dbt tests run inside the flow, so a
failed test stops the run before anything reaches Postgres.

**Why transforms run in DuckDB and not against Postgres directly.** The bronze
models are `read_parquet()` views, and `read_parquet` is a DuckDB function that
Postgres does not have, so `dbt build --target postgres` fails to compile the bronze
layer and skips everything downstream. DuckDB therefore owns every transform and
test; `src/warehouse/load_postgres.py` then copies *only the modeled silver and gold
results* into Postgres for serving, via the DuckDB `postgres` extension. That is the
"push only modeled results into Postgres" rule from `CLAUDE.md` §4 — raw pitch-level
data never enters the warehouse, which is a licensing property as much as a
performance one.

**Why the marts land in a `baseball` schema.** Savepoint-Lab runs one Postgres
database (`sports`) shared by every vertical, so each vertical owns a schema inside
it — tennis publishes `tennis.*`, baseball publishes `baseball.*` (set via
`POSTGRES_SCHEMA`). Publishing to bare `silver`/`gold` schemas would collide with the
next vertical that did the same. The medallion layer is not lost by flattening
because it already lives in the table-name prefix (`sv_`, `gd_`).

## Joining Statcast to the game spine — join on `game_pk`, never on date

Statcast dates a suspended or resumed game by its **original** date; `sv_games`
dates it by the date it reached a final score (deliberately — see the model's own
comment, it is what keeps time-ordered features from peeking ahead). The two
therefore disagree for exactly those games.

Concretely: game_pk 716404 (Mets-Marlins, 2023) sits at `game_date = 2023-10-02` in
the spine, while its 236 pitches sit under `2023-09-28` in Statcast bronze. A
date-based join drops the game; a `game_pk` join is correct. This is also why the
backfill logs one "no rows" date per era — the pitches are not missing, they are
filed under the other date.

Coverage as of 2026-08-31: 8,184,213 pitches over 27,752 games, 2015-2026, and
**zero** games in `sv_games` lack Statcast rows.

## Modeling sequence (PROJECT_CONTEXT §3)

1. **Game-outcome prediction (primary).** Baselines first — home-field prior, Elo,
   Pythagorean, log5 (`src/models/baselines.py`) — then logistic → gradient boosting
   (XGBoost/LightGBM) on team-strength + pitching matchup + park + rest features.
   Judged on **log-loss** and **calibration** (reliability curve) on held-out seasons.
2. **Player & team analysis.** Context-adjusted value via RE24 / WPA from Retrosheet
   events (`src/features/run_expectancy.py`) — both features and "how games are won"
   explanation.
3. **Season/playoff projection.** Monte-Carlo the remaining schedule → playoff → World
   Series odds (`src/simulation/game_engine.py`), calibrated vs. history.
4. **Counterfactual simulation.** Bayesian partial-pooled player rates (PyMC/bambi);
   perturb an input skill and re-simulate to surface improvement levers.

## Baseline results (measured 2026-08-31)

Walk-forward over 2017-2026 (refit on all prior seasons for each held-out season;
regular season only; the 2020 and 2021 folds are excluded from the mean because the
60-game season distorts both). Produced by `python -m src.models.backtest`.

| Model | Log-loss | Brier | Accuracy | ECE |
|-------|----------|-------|----------|-----|
| **Elo** | **0.6788** | 0.2429 | 0.5694 | 0.0146 |
| Pythagorean (prior season) | 0.6821 | 0.2445 | 0.5634 | 0.0171 |
| Log5 (prior-season W-L) | 0.6836 | 0.2452 | 0.5616 | 0.0191 |
| Home-field constant | 0.6914 | 0.2491 | 0.5303 | 0.0079 |

**Elo at 0.6788 is the bar** the gradient-boosted and Bayesian models must beat.
For scale: 0.6931 is a coin flip, so every baseline sits close to it — single MLB
games are genuinely near-random, and honest gains here are small.

Two cautions when reading that table. Home-field has the *best* ECE purely because a
model that always predicts the base rate is trivially calibrated; calibration is a
necessary check, never a ranking metric on its own. And Pythagorean beats Log5
because run differential is the less luck-contaminated measure of the same strength
(year-over-year r = 0.62 vs 0.57 on 2015-2026).

## Statcast form does not beat Elo (measured 2026-09-01)

The first model over the pitch-level data. Features are 30-game rolling team form
(`gd_team_form`) — xwOBA created and allowed, wOBA, run-expectancy added, hard-hit
rate — differenced between the two teams and evaluated on the same walk-forward
harness.

| Model | Log-loss | Brier | Accuracy | ECE |
|-------|----------|-------|----------|-----|
| Elo + Statcast (logistic) | 0.6779 | 0.2425 | 0.5700 | 0.0160 |
| **Elo** | **0.6781** | 0.2426 | 0.5712 | 0.0150 |
| Statcast GBM (incl. Elo) | 0.6800 | 0.2435 | 0.5673 | 0.0179 |
| Statcast GBM (form only) | 0.6832 | 0.2451 | 0.5584 | 0.0156 |

**The honest reading: Statcast team form adds nothing measurable.** The logistic
blend's 0.0002 edge over Elo is not distinguishable from noise — paired per-game
t = 0.49 over 16,608 games, better in only 4 of 7 seasons. Elo remains the model to
beat. Reporting the blend as an improvement would be reading a rounding error.

Two things are worth keeping from the negative result:

- **The GBM lost to Elo while containing Elo as a feature** (0.6800 vs 0.6781). That
  is variance, not absent signal: with games this close to coin flips, a booster with
  enough capacity to find a small edge has more than enough to fit noise that swamps
  it. The linear model in log-odds space could not chase that noise and landed level
  with Elo. When the marginal signal is this thin, model capacity is a liability.
- **Team-level 30-game aggregates are probably the wrong shape.** They smear away the
  single largest per-game factor — the starting pitcher — along with bullpen state,
  rest, and park. A 30-game rolling mean of team xwOBA is close to a slow-moving
  restatement of team strength, which is exactly what Elo already encodes. That
  suggests the next attempt should be pitcher-level, not team-level.

## Evaluation

- **Primary metric:** log-loss vs. an Elo/home-field baseline (must beat it).
- **Calibration:** reliability curves + Brier score on held-out seasons.
- **Projection:** playoff/WS probabilities back-tested against actual outcomes.
- Each shipped model gets a **model card** (`docs/model-card-template.md`).

## Key open questions (tracked from PROJECT_CONTEXT §8)

- How far back to model (Statcast features only exist 2015+; older seasons need
  Retrosheet/Lahman proxies)?
- Park-factor and weather adjustment source + freshness.
- Bullpen/lineup uncertainty for pre-game prediction.
