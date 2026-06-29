"""Unified ingestion CLI: ``saber-ingest <source> ...`` (or ``python -m src.ingestion``).

Aggregates the per-source Typer apps so all compliant pullers share one entrypoint.
"""

from __future__ import annotations

import typer

from src.ingestion import chadwick, lahman, retrosheet, statcast, statsapi

app = typer.Typer(
    add_completion=False,
    help="Savepoint saber-baseball — compliant bronze-layer ingestion.",
)

app.add_typer(lahman.app, name="lahman")
app.add_typer(chadwick.app, name="chadwick")
app.add_typer(statcast.app, name="statcast")
app.add_typer(retrosheet.app, name="retrosheet")
app.add_typer(statsapi.app, name="statsapi")


if __name__ == "__main__":
    app()
