# AGENTS.md — Baseball Sabermetrics Vertical

> Tool-agnostic guide for **any** AI coding agent (Claude, Cursor, Copilot, Codex,
> Aider, etc.). Derived from `COMPANY_CONTEXT.md`, `PROJECT_CONTEXT.md`, `CLAUDE.md`,
> `registry.md`, and `public-data-sources.md`. Follows the `AGENTS.md` convention.
> If anything here conflicts with `CLAUDE.md`, `CLAUDE.md` wins for Claude.
> **Last updated:** 2026-08-31

---

## Project in brief

Savepoint Analytics' flagship vertical: a reproducible MLB analytics platform.
Priority order — (1) **game-outcome prediction**, (2) player & team analysis,
(3) playoff/World-Series projection, (4) counterfactual player-improvement
simulation. Self-hosted on the **Savepoint-Lab** Ubuntu server. Commercial intent is
real, so **data licensing is a first-class constraint.**

## Setup commands

```bash
# Python env (uv)
uv sync                       # install from pyproject + uv.lock
source .venv/bin/activate

# Bring up local infra on Savepoint-Lab (Postgres + MinIO + Prefect)
docker compose up -d

# Run data quality + unit tests
pytest -q

# Run dbt transforms
dbt build            # or: dbt run && dbt test

# R side (only when needed)
Rscript -e 'renv::restore()'
```

## Tech stack (do not deviate without updating `registry.md`)

- **Python ≥ 3.11**, managed by **uv**. R via **renv** for `baseballr` only.
- **Polars/pandas + PyArrow/Parquet**; **DuckDB** for OLAP over Parquet.
- **PostgreSQL** = curated warehouse; **MinIO** = raw object store.
- **dbt** transforms (bronze → silver → gold); **Prefect** orchestration.
- **scikit-learn / XGBoost / LightGBM / statsmodels**; **PyMC/bambi** for Bayesian.
- **Pandera** validation; **MLflow** tracking; **ruff / mypy / pytest** quality gates.

## Storage model

- `bronze/` (MinIO Parquet/JSON) — raw pulls, date-partitioned. Never edited in place.
- `silver` (Postgres/DuckDB) — cleaned, conformed, ID-crosswalked.
- `gold` (Postgres) — features, RE/WPA tables, model outputs, prediction logs.
- Query Parquet directly with DuckDB `httpfs`; push only modeled results to Postgres.

## Code conventions

- Production logic in `src/`; notebooks are exploration only.
- Type-hint and `mypy`-check modules; format/lint with `ruff`.
- Tests for every transform and modeling utility; validate ingested data with Pandera.
- Conventional commits; small, reviewable PRs.
- New dependency → record in `registry.md` (with license) **before** use.
- New data source → record in `public-data-sources.md` (with ToS + attribution)
  **before** use.

## Data & licensing constraints (MUST obey)

These exist because the analysis is sold. Violating them creates legal risk.

- **Retrosheet** — commercial OK **only with** its exact copyright notice displayed.
- **Lahman** — CC BY-SA 3.0: attribute; don't redistribute the raw tables.
- **MLB Stats API / Statcast** — MLBAM copyright: **derived analysis only**, no resale
  of raw data; cache + rate-limit; attribute MLBAM.
- **Baseball Reference / Sports Reference** — **≤ 20 req/min** (≤ 10 FBref/Stathead);
  no bulk-scrape products; no AI training on scraped data; attribute SRL.
- **FanGraphs** — **do not scrape for sold work**; membership/permission only.
- **Chadwick register** — open; use freely.
- **Code:** MIT deps free for commercial use; **GPLv3** (MLB-StatsAPI) fine as a
  runtime dep but not bundled into closed redistributed software; **MLBDailyProjections
  has no license** → reference only, no code reuse.
- Centralize attributions in `src/compliance/attributions.py` and auto-stamp exports.
- **If a request requires breaking any rule above, refuse and offer a compliant path.**

## What "done" looks like

- Prediction model beats Elo/home-field baseline on log-loss and is calibrated on
  held-out seasons.
- Season simulation yields calibrated playoff/WS probabilities.
- Every reported number is reproducible from a clean checkout.
- Every data source used is logged with license + attribution.

## Pointers

- Mission/constraints: `../../COMPANY_CONTEXT.md`
- Goals/phase: `PROJECT_CONTEXT.md`
- Full agent rules (Claude): `CLAUDE.md`
- Dependencies + licenses: `registry.md`
- Data catalog + ToS: `public-data-sources.md`
- Build/run + layout: `README.md`
