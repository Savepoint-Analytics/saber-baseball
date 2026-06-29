# registry.md — Code, Libraries & Tools (Sports/Baseball)

> Inventory of every repo, package, and tool incorporated into this vertical, with
> **license**, language, role, and status. Agents must consult this before adding a
> new dependency. License obligations are summarized here and enforced in `CLAUDE.md`.
> **Last updated:** 2026-06-28

---

## 1. Named source repositories (requested by Alex)

| Repo | Lang | License | Status | Role |
|------|------|---------|--------|------|
| [jldbc/pybaseball](https://github.com/jldbc/pybaseball) | Python | **MIT** | Active (last release 2.2.6, 2023) | **Primary data acquisition.** Scrapes Baseball Reference, Baseball Savant (Statcast), FanGraphs; wraps Lahman, Retrosheet, Chadwick. |
| [BillPetti/baseballr](https://github.com/BillPetti/baseballr) | R | **MIT** (per `LICENSE.md`) | Active (CRAN, 1.6.0, 2024) | R-side acquisition + metric calc (wOBA, FIP, standings). Use when a function isn't in pybaseball. |
| [panzarino/mlbgame](https://github.com/panzarino/mlbgame) | Python | **MIT** (data subject to MLBAM copyright) | **Deprecated / broken** — relies on legacy GD2 GameDay XML endpoints that now return HTTP 403. Last release 2018, Python 2-era. | Reference only. **Do not build on it.** Replaced by MLB-StatsAPI (below). |
| [brendanahart/MLBDailyProjections](https://github.com/brendanahart/MLBDailyProjections) | Python/MATLAB | **NO LICENSE → all rights reserved** | Inactive (2017, Python 2.7, MySQL) | **Inspiration/reference only.** Excellent worked example of a DFS projection pipeline (FanGraphs + ZiPS + odds + gradient descent + lineup optimizer). Do **not** copy its code verbatim into a product without the author's permission. |

> **Important compliance note on MLBDailyProjections:** absence of a `LICENSE` file
> means default copyright — "all rights reserved." We may read it for ideas and learn
> its architecture, but we may not redistribute or directly reuse its source in a
> commercial deliverable. Reimplement concepts from scratch instead.

## 2. Recommended additions

| Repo / Tool | Lang | License | Role |
|------|------|---------|------|
| [toddrob99/MLB-StatsAPI](https://github.com/toddrob99/MLB-StatsAPI) | Python | **GPLv3** | Modern, maintained wrapper for the official MLB Stats API (`statsapi.mlb.com`) — the correct replacement for `mlbgame` (live scores, schedules, boxscores, standings). |
| Official **MLB Stats API** (`https://statsapi.mlb.com/api/`) | HTTP/JSON | MLBAM copyright (see data sources) | Direct, no-scrape access to schedules, live game state, rosters. Preferred for game-level + live data. |
| [Lahman database](https://sabr.org/lahman-database/) | CSV/SQL | **CC BY-SA 3.0** | Season-level historical stats back to 1871. |
| [Retrosheet](https://www.retrosheet.org/) | event files | Free w/ required notice | Play-by-play event data → run expectancy, WPA. |
| [Chadwick Bureau register](https://github.com/chadwickbureau/register) | CSV | Open | Player ID crosswalk (MLBAM ↔ Retrosheet ↔ BBRef ↔ FanGraphs). pybaseball uses it. |
| [baseball.computer](https://baseball.computer/) | DuckDB | Open (built on Retrosheet) | Pre-modeled historical analytical DB queryable via DuckDB. |

> **GPLv3 note (MLB-StatsAPI):** GPLv3 is copyleft. Using it as a runtime dependency
> to *fetch data for internal analysis* is fine. **Do not** statically bundle/modify
> and redistribute it inside a closed-source product. Since Savepoint sells *analysis
> and reports* (not the software), normal use is compliant — but if we ever ship a
> redistributable app that links it, revisit licensing.

## 3. Recommended tech stack (Python-centric, R where useful)

| Layer | Choice | License | Why |
|-------|--------|---------|-----|
| Object storage (raw) | **MinIO** | AGPLv3 (self-host OK) | S3-compatible landing zone for raw JSON / Parquet on Savepoint-Lab. |
| Warehouse (curated) | **PostgreSQL** | PostgreSQL (permissive) | Modeled tables, dims, features, model outputs. |
| Local OLAP engine | **DuckDB** | MIT | Query Parquet in MinIO directly (`httpfs`) — fast analytics without loading all into Postgres. |
| Transformation | **dbt-core** (`dbt-postgres` / `dbt-duckdb`) | Apache-2.0 | Versioned, tested, documented SQL transforms; medallion modeling. |
| Orchestration | **Dagster** (or cron+Make to start) | Apache-2.0 | Asset-based pipelines with lineage; lightweight for solo ops. |
| DataFrames | **pandas** + **Polars** + **PyArrow** | BSD / MIT / Apache | Polars for large Statcast pulls; Arrow/Parquet as interchange. |
| Modeling | **scikit-learn, XGBoost, LightGBM, statsmodels** | BSD/Apache/MIT | Outcome models, baselines. |
| Bayesian | **PyMC / bambi** | Apache-2.0 | Partial-pooled player rates, counterfactuals. |
| Data validation | **Pandera** or **Great Expectations** | MIT / Apache | Quality gates on ingested data. |
| Experiment tracking | **MLflow** | Apache-2.0 | Model versions, metrics, calibration history. |
| Serving / BI | **Streamlit** + **Metabase** | Apache / AGPL | Dashboards + ad-hoc BI over Postgres. |
| Python env | **uv** (or Poetry) | MIT/Apache | Fast, reproducible deps + lockfile. |
| R env | **renv** + `baseballr` | MIT | Reproducible R for the few R-only tasks. |
| Containers | **Docker Compose** | Apache-2.0 | Run Postgres + MinIO + Dagster on Savepoint-Lab. |

## 4. License compliance summary (code)

- **Permissive (MIT/BSD/Apache):** pybaseball, baseballr, mlbgame, DuckDB, dbt,
  Dagster, scikit-learn, etc. — free for commercial use; retain copyright notices.
- **Copyleft (GPLv3):** MLB-StatsAPI — fine as a runtime dependency for producing
  analysis; do not redistribute as part of a closed binary.
- **Copyleft (AGPLv3):** MinIO server, Metabase — fine to self-host and use; AGPL
  obligations trigger only if we offer a *modified* version of the software itself as
  a network service. Running unmodified is compliant.
- **No license / all-rights-reserved:** MLBDailyProjections — reference only; no
  code reuse without permission.

> The harder compliance question is the **data**, not the code. See
> `public-data-sources.md` and `CLAUDE.md` §Compliance.

## 5. Phase-1 scaffold dependencies (added 2026-06-28)

Pinned in `pyproject.toml`. All permissive and free for commercial use; retain notices.

| Package | License | Role |
|---------|---------|------|
| pyarrow | Apache-2.0 | Parquet interchange |
| boto3 | Apache-2.0 | S3/MinIO client |
| s3fs | BSD-3 | fsspec S3/MinIO filesystem for Parquet IO |
| requests | Apache-2.0 | HTTP (StatsAPI JSON) |
| tenacity | Apache-2.0 | Retry/backoff |
| pydantic / pydantic-settings | MIT | Typed, env-driven config |
| python-dotenv | BSD-3 | `.env` loading |
| platformdirs | MIT | Cache dir resolution |
| typer | MIT | Ingestion CLI |
| loguru | MIT | Logging |
| ruff / mypy / pytest / pytest-cov | MIT | Quality gates (dev) |

> GPLv3 **MLB-StatsAPI** and AGPLv3 **MinIO** are used per §2/§4 above (runtime / self-host
> unmodified). No new copyleft obligations introduced by the scaffold.
