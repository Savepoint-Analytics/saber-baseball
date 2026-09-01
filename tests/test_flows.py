"""Tests for the Prefect orchestration layer.

CI installs only the `dev` and `modeling` extras, so Prefect may be absent — these
skip rather than fail in that case (the same way the orchestrator was untested under
Dagster). Run locally with `uv sync --extra orchestration`.
"""

from __future__ import annotations

import pytest

prefect = pytest.importorskip("prefect")

from orchestration import flows  # noqa: E402


def test_dbt_dir_points_at_a_real_dbt_project():
    """DBT_DIR is resolved from __file__, so a moved module would break silently."""
    assert (flows.DBT_DIR / "dbt_project.yml").is_file()


def test_daily_pipeline_exposes_the_documented_parameters():
    """The Makefile `pipeline` target and Compose rely on these defaults."""
    params = flows.daily_pipeline.parameters.properties
    assert set(params) == {"season", "day", "include_reference", "publish_to_postgres"}
    assert params["include_reference"]["default"] is True
    assert params["publish_to_postgres"]["default"] is True


def test_ingest_tasks_retry_but_not_aggressively():
    """Retries must stay gentle: upstreams are rate-limited by ToS (CLAUDE.md §5)."""
    for task in (flows.chadwick_register, flows.lahman_tables, flows.statsapi_schedule):
        assert task.retries == 2
        assert task.retry_delay_seconds == 60


def test_dbt_build_fails_loudly_when_dbt_is_missing(monkeypatch):
    """A missing dbt must raise, not silently no-op and let stale marts publish."""
    monkeypatch.setattr(flows.shutil, "which", lambda _: None)

    with pytest.raises(RuntimeError, match="dbt not on PATH"):
        flows.dbt_build.fn()
