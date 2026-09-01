# CLAUDE.md — Operating Rules for Agents (Sports/Baseball)

> Read order: `../../COMPANY_CONTEXT.md` → `PROJECT_CONTEXT.md` → this file.
> This is the constitution for any AI agent working in the baseball vertical. The
> tool-agnostic mirror of these rules lives in `AGENTS.md`.
> **Last updated:** 2026-08-31

---

## 1. Mission in one sentence

Help build a reproducible MLB analytics platform — game-outcome prediction first,
then player/team analysis and counterfactual simulation — while strictly honoring
data licensing so the output can be sold affordably and legally.

## 2. Golden rules

1. **Reproducibility over cleverness.** Every metric must trace from raw source →
   transform → output and re-run from a clean checkout.
2. **Compliance is non-negotiable.** Never use a data source in a way that violates
   §5. When unsure, stop and ask.
3. **Python-first.** Analysis, transforms, pipelines, automation = Python. Use R only
   where it is clearly better (e.g., `baseballr` for functions absent in pybaseball).
4. **Sell insight, not raw data.** Deliverables are derived analysis/models/visuals,
   never repackaged proprietary feeds.
5. **Cache and rate-limit.** Be a good citizen of every upstream site.

## 3. Tech-stack conventions

- **Language:** Python ≥ 3.11, managed with **uv** (lockfile committed). R via **renv**.
- **DataFrames:** Polars for large/pitch-level data; pandas where ecosystem requires it; Parquet + PyArrow for interchange.
- **Transforms:** **dbt** models (medallion: bronze → silver → gold). SQL transforms are tested and documented.
- **Orchestration:** **Prefect** flows (start with cron + Make if Prefect is overkill early).
  Prefect is the orchestrator across *every* Savepoint vertical — see `registry.md` §6.
- **Modeling:** scikit-learn / XGBoost / LightGBM / statsmodels; **PyMC / bambi** for Bayesian player rates. Track runs in **MLflow**.
- **Validation:** **Pandera** schemas (or Great Expectations) gate every ingested table.
- **Style:** `ruff` (lint+format), `mypy` for typed modules, `pytest` for tests. Notebooks are for exploration only — production logic lives in `src/`.

## 4. Storage architecture (recommended) — Savepoint-Lab

**Decision: Hybrid lakehouse on the Savepoint-Lab Ubuntu server — MinIO + PostgreSQL,
with DuckDB as the query engine.** Rationale documented so future agents understand
*why*, not just *what*:

- **MinIO (object storage) = raw/bronze landing zone.** Statcast is ~700k+ pitches per
  season with wide schemas; raw API JSON and pitch-level Parquet belong in cheap,
  schema-on-read object storage, not in relational tables. S3-compatible, so DuckDB,
  Polars, and dbt-duckdb all read it natively. Date-partition the keys
  (`bronze/statcast/season=2025/date=2025-06-24.parquet`).
- **PostgreSQL = curated silver + gold warehouse.** Cleaned, conformed, queryable
  tables: player/team dimensions, RE/WPA tables, engineered features, model outputs,
  prediction logs. This is what dashboards, BI, and the prediction service query.
- **DuckDB = analytical engine.** Query Parquet in MinIO directly via `httpfs` for
  heavy OLAP/feature work without bloating Postgres; push only modeled results into
  Postgres. `dbt-duckdb` for lake transforms, `dbt-postgres` for warehouse marts.

**Why not one or the other?**
- *Postgres-only:* pitch-level raw data bloats the DB, indexing/vacuum pain, awkward
  for wide evolving schemas.
- *MinIO-only:* no clean relational layer for BI tools, joins, and serving; weaker
  constraints/integrity for curated marts.
- *Hybrid* gives cheap durable raw storage **and** a clean serving warehouse, all
  self-hosted (≈ $0 recurring) — directly serving the "affordable" mission.

**Deploy:** Docker Compose on Savepoint-Lab running `postgres`, `minio`, `prefect-server`,
and `prefect-flows`.
Back up Postgres (`pg_dump` to MinIO) and snapshot MinIO buckets. Keep secrets in
`.env` (git-ignored); never commit credentials.

> Decision is revisitable: if data ever outgrows a single box, the MinIO/Parquet
> layer ports cleanly to S3 + a cloud warehouse without rewriting transforms.

## 5. Compliance rules (MUST follow) — and why

These derive from `public-data-sources.md` and `registry.md`. Document the reason in
code comments when a rule shapes an implementation.

**Data-source rules:**
- **Retrosheet** — ✅ commercial OK. **MUST** display the exact Retrosheet notice in
  any deliverable using it. *Why: Retrosheet grants free commercial use only on
  condition of the prominent copyright acknowledgment.*
- **Lahman** — ✅ commercial OK with attribution. Do **not** redistribute the Lahman
  tables as a dataset; if a derived *dataset* is ever shipped, it must carry CC BY-SA
  3.0. *Why: ShareAlike attaches to redistributed derivatives of the data, not to our
  analysis/reports.*
- **MLB Stats API / Statcast (MLBAM)** — ⚠️ derived analysis only. **Never** resell or
  redistribute raw MLB/Statcast data. Cache locally; rate-limit; attribute MLBAM.
  *Why: data is under MLBAM copyright, licensed for personal/non-commercial use;
  commercial redistribution needs MLBAM consent.*
- **Baseball Reference / Sports Reference** — ⚠️ **≤ 20 requests/min** (≤ 10 for
  FBref/Stathead); no bulk-scrape-backed products; no training generative AI on
  scraped SR data; attribute SRL. Prefer Retrosheet/Lahman for systematic pulls.
  *Why: their Terms of Use prohibit it and enforce rate caps with day-long blocks.*
- **FanGraphs** — ❌ **Do not scrape FanGraphs for any sold deliverable.** Use only via
  membership/permission, or treat as human reference. *Why: FanGraphs explicitly
  prohibits scraping, APIs, and automated export.*
- **Chadwick register** — ✅ open; use freely for ID crosswalks.

**Code-license rules:**
- **MIT deps (pybaseball, baseballr, mlbgame, DuckDB, dbt, Prefect…)** — ✅ commercial
  OK; retain copyright/license notices.
- **GPLv3 (MLB-StatsAPI)** — ✅ as a runtime dependency for producing analysis. Do
  **not** bundle a modified copy inside a closed-source redistributed product. *Why:
  GPLv3 copyleft triggers on distribution of derivative software, not on using it to
  generate reports.*
- **AGPLv3 (MinIO, Metabase)** — ✅ self-host unmodified. AGPL network-service
  obligations trigger only if we distribute a *modified* version as a service.
- **MLBDailyProjections (no license)** — ❌ **reference only.** No code reuse in a
  deliverable without the author's written permission; reimplement ideas from scratch.

**Operational guardrails for agents:**
- Centralize every source's attribution string in `src/compliance/attributions.py`
  and stamp it onto exported reports/dashboards automatically.
- Enforce rate limits in the ingestion layer (shared throttle/backoff); enable
  `pybaseball.cache` to avoid re-hitting sources.
- If a task would require violating any rule above (e.g., "scrape all of FanGraphs
  into a sellable table"), **refuse and propose a compliant alternative.**

## 6. Working agreements

- Small, reviewable changes; conventional commits.
- New dependency → add to `registry.md` with its license before use.
- New data source → add to `public-data-sources.md` with ToS + attribution before use.
- Write tests for transforms and modeling utilities; validate data with Pandera.
- Prefer explicit, documented SQL/dbt over hidden notebook logic.

## 7. Pointers

- Project goals & phase: `PROJECT_CONTEXT.md`
- Company mission & constraints: `../../COMPANY_CONTEXT.md`
- Dependencies + licenses: `registry.md`
- Data catalog + ToS: `public-data-sources.md`
- Build/run + layout: `README.md`
