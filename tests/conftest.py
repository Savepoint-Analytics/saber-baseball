"""Shared fixtures and the lake guard.

Most of this suite has two halves: pure-Python tests that run anywhere, and
lake-backed tests that read the DuckDB warehouse `make dbt-duckdb` builds. The
second half has to be skipped when there is no lake — CI never builds one — and
that is what ``requires_lake`` is for.

**It checks the lake's schema, not just its existence.** A guard that only asks
"does saber.duckdb exist?" passes on a lake built by an older checkout, and the
tests then fail deep inside DuckDB with a binder error naming a column nobody
asked about. That is exactly what happened when the win-probability work landed:
a developer with a lake built before the branch saw seven failures that said
nothing about the real problem, which was simply that the lake needed rebuilding.
Declaring the relations a test needs turns that into a skip that says so.
"""

from __future__ import annotations

import functools

import pytest

from src.config import settings

LAKE = (settings.data_dir / "saber.duckdb").resolve()

REBUILD_HINT = "run `make dbt-duckdb` to rebuild it"


@functools.lru_cache(maxsize=1)
def lake_schema() -> dict[str, frozenset[str]]:
    """``{"schema.table": {columns}}`` for the lake, or empty if there is none.

    Read once per session and cached: the guard runs at import time for every
    test module, and reopening the file each time would be paid on every
    collection.
    """
    if not LAKE.exists():
        return {}
    import duckdb

    con = duckdb.connect(str(LAKE), read_only=True)
    try:
        rows = con.sql(
            "select table_schema, table_name, column_name from information_schema.columns"
        ).fetchall()
    finally:
        con.close()
    schema: dict[str, set[str]] = {}
    for table_schema, table, column in rows:
        schema.setdefault(f"{table_schema}.{table}", set()).add(column)
    return {table: frozenset(columns) for table, columns in schema.items()}


def lake_skip_reason(*relations: str) -> str | None:
    """Why these relations are unavailable, or None if the lake can serve them.

    Each relation is ``"schema.table"`` or ``"schema.table.column"``.
    """
    schema = lake_schema()
    if not schema:
        return f"DuckDB lake not built ({LAKE})"
    for relation in relations:
        parts = relation.split(".")
        table = ".".join(parts[:2])
        if table not in schema:
            return f"lake predates this test: {table} is missing — {REBUILD_HINT}"
        if len(parts) == 3 and parts[2] not in schema[table]:
            return f"lake predates this test: {table} has no {parts[2]} column — {REBUILD_HINT}"
    return None


def requires_lake(*relations: str) -> pytest.MarkDecorator:
    """Skip unless the lake exists and carries every relation named."""
    reason = lake_skip_reason(*relations)
    return pytest.mark.skipif(reason is not None, reason=reason or "")
