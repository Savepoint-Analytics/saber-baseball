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

## Current status — Phases 1, 3 and 4 done; Phase 2 started (as of 2026-09-15)

Phase 0 (foundations) and Phase 1 (ingestion) are complete, and the work ran ahead
of the roadmap's order: prediction (Phase 3) and season simulation (Phase 4) are built
and backtested, and player evaluation (Phase 2) has its first marts. What this
checkout ships:

- `src/ingestion/` — compliant pullers for **Statcast**, **Retrosheet**, **Lahman**,
  **Chadwick**, the **MLB Stats API** and the team dimension, through a shared
  rate-limit + on-disk cache + backoff layer (`_throttle.py`). 8.2M pitches,
  2015–2026, land in MinIO on Savepoint-Lab.
- `dbt/` — bronze → silver → gold in DuckDB, 168 tests; `src/warehouse/` publishes
  the curated marts into the `baseball` schema of the shared `sports` Postgres.
  `orchestration/flows.py` runs ingest → dbt → publish daily at 09:00 ET (Prefect).
- `src/models/` — nine outcome-model families, walk-forward backtested. **Elo is still
  the bar** (0.6781 log-loss); Elo + starter form reaches 0.6769, 6 of 7 seasons, not
  yet significant. Playoff odds are calibration-tested and carry their caveat.
- `src/simulation/` — the plate-appearance engine (~7,100 games/sec), season
  projection to playoff/World Series odds, rating uncertainty propagated.
- `src/features/` + the player marts — **RE24 and WPA per player-season**, season
  linear weights, an analytically solved win-probability/leverage table,
  `gd_batter_season` / `gd_pitcher_season` with park-adjusted runs above average,
  and `sv_players` (Chadwick + Lahman + Statcast). `make player-value` prints
  leaders or a value card that shows raw production next to shrunk talent.
- `src/serving/daily_report.py` — today's win probabilities, projected scores and
  playoff odds, attribution-stamped (`make today`).
- `src/compliance/attributions.py` — canonical attribution strings, auto-stamped onto
  every export. `src/schemas/` — Pandera schemas gating ingested tables.

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
│   ├── features/                 # RE24 + WPA mirrors, win-probability solver, value cards
│   ├── models/                   # baselines, outcome models, backtests, playoff calibration
│   ├── simulation/               # PA-level engine, season projection
│   └── serving/                  # daily prediction report; Streamlit shell
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

# 5. Use it
make today                      # today's win probabilities + playoff odds
make player-value SEASON=2025   # batter / pitcher leaders; PLAYER=660271 for a card
make backtest                   # walk-forward backtest of the outcome models
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
2. **Phase 1 — Ingestion (done):** compliant bronze pipelines (Statcast, Retrosheet,
   Lahman, Chadwick, Stats API) into MinIO/Parquet; dbt silver/gold in DuckDB,
   published to Postgres; Prefect daily chain.
3. **Phase 2 — Player & team analysis (in progress):** RE24 is built per season from
   Statcast plate appearances (no Retrosheet needed for 2015+) and credited to every
   batter and pitcher (`gd_player_re24`); season linear weights reproduce the textbook
   values (1B .47 … HR 1.39) without being told them; `gd_batter_season` and
   `gd_pitcher_season` carry rates, wOBA/xwOBA, context-included and context-neutral
   runs above average, and a PA-weighted park correction. `src/features/player_value.py`
   joins the odds-ratio model's shrunk talent rates. Win probability and leverage
   are **solved analytically** from the same components (`gd_win_probability`,
   martingale-exact, calibrated to 0.011 against real games) and credited as WPA,
   WPA/LI and mean leverage. **Not yet:** defence, baserunning, salary, and any
   serving surface beyond the CLI.
4. **Phase 3 — Prediction (baselines done, still trying to beat Elo):** nine model
   families backtested walk-forward (`make backtest`). Elo (0.6781) survives all of
   them; Elo + starter form (0.6769, 6/7 seasons) is promising but not significant.
   Bullpen, park/weather and batter × pitch-family features were measured and rejected.
   See `docs/methodology.md`.
5. **Phase 4 — Season simulation (done, with caveats):** the plate-appearance engine
   simulates complete games at ~7,100 games/sec and reproduces run scoring, home-field
   advantage and 1-run frequency; it remains ~6% under-dispersed, and six mechanisms
   were measured without closing the gap. `make season` Monte-Carlos the schedule to
   playoff and World Series odds; `make season-backtest` scores them from 1 September
   2023–25 — locks and also-rans are well calibrated, the 75–95% band is overconfident,
   and the report says so. Plain-language write-ups in [`docs/explainers/`](docs/explainers/).
6. **Phase 5 — Counterfactuals (next):** perturb a player's shrunk rate inputs,
   re-simulate, surface improvement levers; package as affordable analysis products.

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
