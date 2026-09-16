"""Gold: win probability and leverage index for every game situation, per season.

Grain: (season, inning, half, score_diff, state). ``inning`` 10 stands for any
extra inning; ``state`` 24 means the half-inning is over (the value once the third
out is recorded, which is what a PA that ends a half-inning is scored against).
``score_diff`` is home minus away, clamped to +-20. ``leverage`` is null for state
24, since no plate appearance starts there.

A dbt *Python* model, because the table is solved rather than aggregated: an
exact Markov solve over ``src.features.win_probability`` from the odds-ratio
model's league/state/side rates and ``gd_base_transitions``. Running it inside the
DAG keeps it downstream of the tables it reads and upstream of the WPA marts, and
means a lake rebuild regenerates it without a separate step. dbt-duckdb executes
this in-process on the same connection, which is why the solver takes a
connection rather than opening the lake itself.
"""

from __future__ import annotations

import pandas as pd


def model(dbt, session):
    dbt.config(materialized="table")
    # Declared for the DAG: the solver reads both by name through the session.
    dbt.ref("sv_plate_appearances")
    dbt.ref("gd_base_transitions")

    from src.features.win_probability import build_for_season

    seasons = [
        int(r[0])
        for r in session.sql(
            "select distinct season from main_silver.sv_plate_appearances "
            "where season_phase = 'regular' order by 1"
        ).fetchall()
    ]
    frames = [build_for_season(season, con=session).to_frame() for season in seasons]
    return pd.concat(frames, ignore_index=True)
