# Savepoint Analytics — saber-baseball common commands.
# `make help` lists targets. Python is driven by uv; infra by Docker Compose.

.DEFAULT_GOAL := help
SHELL := /bin/bash
SEASON ?= 2025

# Backfill range. 2015 is the Statcast-era boundary (PROJECT_CONTEXT §4); the end
# defaults to the current year so the target does not go stale.
BACKFILL_START ?= 2015
BACKFILL_END ?= $(shell date +%Y)

.PHONY: help setup sync infra-up infra-down ingest ingest-lahman ingest-chadwick \
        ingest-statcast ingest-retrosheet ingest-statsapi backfill dbt dbt-duckdb \
        publish sync-minio warehouse flows pipeline test lint fmt typecheck serve clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

setup: ## Create venv + install all dependency groups
	uv sync --all-extras

sync: ## Install runtime deps only (Phase-1 ingestion)
	uv sync

infra-up: ## Start Postgres + MinIO + Prefect on Savepoint-Lab
	docker compose up -d

infra-down: ## Stop the local stack
	docker compose down

ingest: ingest-chadwick ingest-lahman ingest-statcast ## Run the core compliant pulls for $(SEASON)

ingest-lahman: ## Pull Lahman season-level history
	uv run python -m src.ingestion.lahman

ingest-chadwick: ## Pull Chadwick player-ID crosswalk
	uv run python -m src.ingestion.chadwick

ingest-statcast: ## Pull a Statcast window (override START/END)
	uv run python -m src.ingestion.statcast --season $(SEASON)

ingest-retrosheet: ## Pull Retrosheet events for $(SEASON)
	uv run python -m src.ingestion.retrosheet --season $(SEASON)

ingest-statsapi: ## Pull MLB Stats API schedule for $(SEASON)
	uv run python -m src.ingestion.statsapi --season $(SEASON)

backfill: ## Backfill schedules for BACKFILL_START..BACKFILL_END (default 2015..current)
	uv run python -m src.ingestion.statsapi \
		--start-season $(BACKFILL_START) --end-season $(BACKFILL_END)

# dbt reads config from the environment, not from .env — without this it silently
# falls back to every env_var() default (bucket `saber-bronze`, endpoint
# `localhost:9000`) and a MinIO-backed build fails or, worse, reads the wrong data.
# SABER_DATA_DIR is relative in .env, but dbt runs from dbt/, so it must be made
# absolute here or a local-backend build looks for ./dbt/data/bronze and finds
# nothing. Values already in the environment are overwritten by .env — .env is
# the source of truth; change it there rather than prefixing the make call.
ENVLOAD = set -a && . ./.env && SABER_DATA_DIR=$$(cd "$${SABER_DATA_DIR:-./data}" && pwd) && set +a

dbt: ## Build dbt models against Postgres
	$(ENVLOAD) && cd dbt && uv run dbt build --target postgres

dbt-duckdb: ## Build dbt bronze/silver models against DuckDB
	$(ENVLOAD) && cd dbt && uv run dbt build --target duckdb

sync-minio: ## Mirror the local bronze tree into MinIO on Savepoint-Lab
	uv run python -m src.warehouse.sync_minio

publish: ## Copy modeled silver/gold tables from DuckDB into the Postgres warehouse
	uv run python -m src.warehouse.load_postgres

warehouse: dbt-duckdb publish ## Transform in DuckDB, then publish results to Postgres

# Prefect reads PREFECT_API_URL from the process environment, not from our settings
# object, so these need .env exported the same way the dbt targets do.
flows: ## Serve the scheduled Prefect deployment (long-running; Ctrl-C to stop)
	$(ENVLOAD) && uv run python -m orchestration.flows

pipeline: ## Run the full ingest -> dbt -> publish chain once, right now
	$(ENVLOAD) && uv run python -c "from orchestration.flows import daily_pipeline; daily_pipeline()"

test: ## Run pytest
	uv run pytest

lint: ## Lint with ruff
	uv run ruff check src tests

fmt: ## Format with ruff
	uv run ruff format src tests

typecheck: ## Type-check with mypy
	uv run mypy src

serve: ## Launch the Streamlit dashboard
	uv run streamlit run src/serving/app.py

clean: ## Remove caches and build artifacts
	rm -rf .ruff_cache .mypy_cache .pytest_cache **/__pycache__ dbt/target dbt/logs
