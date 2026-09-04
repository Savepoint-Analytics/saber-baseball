# Savepoint Analytics — Baseball Sabermetrics (`saber-baseball`)

Reproducible, empirical MLB analytics: **predict game outcomes**, explain *how* games
are won, project **playoff and World Series** odds, and run **counterfactual
simulations** for player evaluation and improvement. The flagship vertical of
Savepoint Analytics, self-hosted on the **Savepoint-Lab** Ubuntu server.

> **New here? Read in this order:** `COMPANY_CONTEXT.md` → `PROJECT_CONTEXT.md`
> → `CLAUDE.md` (or `AGENTS.md`) → `registry.md` → `public-data-sources.md`.

---

## Why this project exists

Convert deep, lived baseball knowledge into *empirical* understanding usable for
coaching, player analysis, and prediction — and prove that Savepoint's multi-vertical
analytics platform works end to end. Priority: **game-outcome prediction first**, then
player/team analysis, then season projection, then counterfactual simulation.

## Current status — Phase 1 (ingestion)

Phase 0 (foundations/scaffolding) is complete. This checkout ships a working,
**compliant** bronze-layer ingestion stack:

- `src/ingestion/` — runnable pullers for **Statcast**, **Retrosheet**, **Lahman**,
  **Chadwick**, and the **MLB Stats API**, all routed through a shared rate-limit +
  on-disk cache + backoff layer (`_throttle.py`).
- `src/compliance/attributions.py` — canonical attribution strings, auto-stamped onto
  every export.
- `src/schemas/` — Pandera schemas that gate ingested tables.
- `orchestration/flows.py` — **Prefect** flows chaining ingestion → `dbt build` →
  Postgres publish, on a 09:00 ET daily schedule.
- `src/models/baselines.py` + `src/models/backtest.py` — **Phase 3 baselines**
  (home-field, Log5, Pythagorean, Elo) and the walk-forward log-loss/calibration
  harness. Elo sets the bar at **0.6788 log-loss**; see `docs/methodology.md`.
- `dbt/`, `src/{features,simulation,serving}/` — scaffolded with documented stubs
  for the later phases.

## Architecture at a glance

```
            ┌─────────────── Savepoint-Lab (Ubuntu, Docker Compose) ───────────────┐
            │                                                                       │
  Sources ──┼─► Ingestion (Python)                                                  │
  Retrosheet│      pybaseball / MLB-StatsAPI / baseballr                            │
  Lahman    │            │                                                          │
  Statcast  │            ▼                                                          │
  Chadwick  │   ┌───────────────┐   dbt-duckdb   ┌──────────────┐   dbt-postgres    │
            │   │ MinIO (bronze)│ ─────────────► │ DuckDB (OLAP)│ ───────────────►  │
            │   │ raw Parquet/  │                │ feature work │      ┌──────────┐ │
            │   │ JSON, dated   │                └──────────────┘      │ Postgres │ │
            │   └───────────────┘                                      │ silver + │ │
            │                                                          │  gold    │ │
            │   Prefect orchestrates every flow · MLflow tracks models  └────┬─────┘ │
            └──────────────────────────────────────────────────────────────┼───────┘
                                                                            ▼
                                              Models (sklearn/XGB/PyMC) · Sim engine
                                              Serving: Streamlit dashboards · Metabase BI
```

- **MinIO** = cheap raw landing zone (pitch-level Statcast, raw API JSON).
- **PostgreSQL** = curated warehouse (dims, features, model outputs) for serving/BI.
- **DuckDB** = fast OLAP over Parquet without bloating Postgres.
- **dbt** = tested, documented bronze→silver→gold transforms. **Prefect** = orchestration.

See `CLAUDE.md` §4 for the full rationale (and why not Postgres-only or MinIO-only).

## Repository layout

```
saber-baseball/
├── README.md
├── COMPANY_CONTEXT.md            # company mission & constraints
├── PROJECT_CONTEXT.md            # vertical goals & phase
├── CLAUDE.md                     # operating rules for Claude
├── AGENTS.md                     # tool-agnostic agent guide
├── registry.md                   # code/tool inventory + licenses
├── public-data-sources.md        # data catalog + ToS + attributions
│
├── pyproject.toml                # uv-managed deps + lockfile (uv.lock)
├── docker-compose.yml            # postgres + minio + prefect
├── .env.example                  # config template (never commit real .env)
├── .gitignore
├── Makefile                      # common commands (ingest, build, test, sim)
├── LICENSE                       # MIT (covers OUR code, not third-party data)
│
├── src/
│   ├── config.py                 # env-driven settings (paths, MinIO, Postgres)
│   ├── ingestion/                # source pullers, one module per source
│   │   ├── _throttle.py          # shared rate-limit + cache + backoff
│   │   ├── statcast.py
│   │   ├── retrosheet.py
│   │   ├── lahman.py
│   │   ├── statsapi.py           # MLB-StatsAPI / statsapi.mlb.com
│   │   └── chadwick.py           # player ID crosswalk
│   ├── compliance/
│   │   └── attributions.py       # canonical attributions, auto-stamped on exports
│   ├── schemas/                  # Pandera schemas gating ingested tables
│   ├── features/                 # feature engineering (RE, WPA, matchups) [stub]
│   ├── models/                   # outcome, team-strength, Bayesian rates [stub]
│   ├── simulation/               # PA-level Markov / Monte-Carlo engine [stub]
│   └── serving/                  # Streamlit apps, report builders [stub]
│
├── dbt/                          # dbt project (bronze → silver → gold)
├── orchestration/                # Prefect flows (tasks, schedules)
├── r/                            # R-only tasks (renv); baseballr helpers
├── notebooks/                    # exploration ONLY — no production logic
├── tests/                        # pytest: transforms, models, Pandera schemas
├── data/                         # local scratch (git-ignored)
└── docs/                         # methodology, model cards, calibration reports
```

## Quickstart

```bash
# 1. Environment (Python >= 3.11, uv)
uv sync && source .venv/bin/activate
cp .env.example .env            # fill in Postgres + MinIO credentials

# 2. Infra on Savepoint-Lab
docker compose up -d            # postgres, minio, prefect

# 3. First pull (compliant: Retrosheet + Lahman + Chadwick + Statcast-derived)
make ingest SEASON=2025

# 4. Transform + test
make warehouse                  # dbt build in DuckDB, then publish results to Postgres
make test

# 5. Explore
make serve                      # streamlit run src/serving/app.py
```

Orchestration (Prefect) runs the whole chain — ingest → dbt → publish — for you:

```bash
make pipeline                   # run the full chain once, right now
make flows                      # serve the 09:00 ET daily schedule (long-running)
```

`docker compose up -d` also brings up the Prefect API/UI at
<http://localhost:4200> (`PREFECT_PORT`) plus a `prefect-flows` service that serves
the daily deployment.

To try ingestion directly without Make:

```bash
python -m src.ingestion.lahman                       # season-level history
python -m src.ingestion.chadwick                     # ID crosswalk
python -m src.ingestion.statcast --start 2025-04-01 --end 2025-04-07
python -m src.ingestion.retrosheet --season 2024
python -m src.ingestion.statsapi --season 2025
python -m src.ingestion.statsapi --start-season 2015                 # multi-season backfill
```

To rebuild the full game history a model trains on (Statcast era → current season):

```bash
make backfill                            # 2015..current year, one partition per season
make backfill BACKFILL_START=2021        # or override the range
make dbt-duckdb                          # bronze → silver → gold
```

## Roadmap

1. **Phase 0 — Foundations (done):** context files, registry, storage, skeleton.
2. **Phase 1 — Ingestion (current):** compliant bronze pipelines (Statcast, Retrosheet,
   Lahman, Chadwick) into MinIO/Parquet; Pandera-validated silver in Postgres.
3. **Phase 2 — Run expectancy & WPA:** RE24/WPA tables from Retrosheet events.
4. **Phase 3 — Prediction (in progress):** home-field, Elo, Pythagorean and log5 are
   backtested (`make backtest`), plus Statcast team-form and **starting-pitcher**
   models. Team form does not beat Elo; starter form (rolling xwOBA allowed, split by
   times through the order) reaches 0.6769 vs Elo's 0.6781 and wins 6 of 7 seasons,
   but is not yet statistically significant. Bullpen form and park/weather were also
   tried and make it significantly **worse**. See `docs/methodology.md`.
5. **Phase 4 — Season simulation (engine done, season sim next):** the
   plate-appearance engine simulates complete games at ~7,100 games/sec — PA outcome
   model (odds-ratio), base-out transition matrix, lineups, substitution policy and
   home-field advantage. It reproduces league run scoring to within 1%, home-field
   advantage to 0.2pp, and 1-run game frequency to 0.4pp. Park and weather are wired
   in as per-game run multipliers, and day-to-day pitcher form as a per-outing one.
   The engine remains ~6% under-dispersed: park/weather supply ~25% of the gap and
   pitcher form ~5%, both measured rather than tuned (see `docs/methodology.md`).
   Plain-language write-ups live in [`docs/explainers/`](docs/explainers/). Next:
   Monte-Carlo the schedule → playoff & World Series odds.
6. **Phase 5 — Counterfactuals:** perturb player rate inputs, re-simulate, surface
   improvement levers; package as affordable analysis products.

## Data & licensing — the short version

The code we depend on is mostly MIT (free for commercial use). **The data is not the
same as the code.** Before using any source, check `public-data-sources.md`. Headlines:

- ✅ **Retrosheet** (commercial OK + required notice), **Lahman** (CC BY-SA 3.0),
  **Chadwick** (open) — the safe foundation.
- ⚠️ **MLB Stats API / Statcast** — derived analysis only, no resale of raw data,
  attribute MLBAM, rate-limit + cache.
- ⚠️ **Baseball Reference** — ≤ 20 req/min, attributed, no bulk-scrape products.
- ❌ **FanGraphs** — do not scrape for sold deliverables (membership/permission only).

**Rule of thumb: sell the analysis, never the raw feed; always attach attributions.**
Full obligations and the *reasons* are documented in `CLAUDE.md` §5 and `AGENTS.md`.
Programmatic enforcement lives in `src/ingestion/_throttle.py` (rate caps + cache) and
`src/compliance/attributions.py` (auto-stamped notices).

## Attribution (when publishing results that use these sources)

> "The information used here was obtained free of charge from and is copyrighted by
> Retrosheet. Interested parties may contact Retrosheet at www.retrosheet.org."
>
> "Data from the Lahman Baseball Database, © 1996–2024 Sean Lahman, CC BY-SA 3.0."
>
> "Includes data © MLB Advanced Media / Baseball Savant." ·
> "Stats courtesy of Baseball-Reference.com (Sports Reference LLC)."

## License

Our original code is released under the **MIT License** (see `LICENSE`). This applies
only to the software in this repository — **not** to any third-party data it retrieves,
which remains governed by the terms in `public-data-sources.md`.
