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
        → dbt (duckdb) bronze views → silver (Postgres) → gold marts
        → src/features, src/models, src/simulation → MLflow + serving
```

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
