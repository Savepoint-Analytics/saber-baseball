-- Bronze view over pitch-level Statcast Parquet (dbt-duckdb).
-- Read-only passthrough; conforming happens in silver. ~8.2M rows, 2015-2026.
--
-- Only the columns downstream models actually use are selected. The raw feed is
-- ~90 columns wide and selecting * here makes every downstream scan read all of
-- them off object storage, which is the dominant cost when bronze lives in MinIO.
{{ config(materialized='view') }}

select
    cast(game_pk as bigint)                      as game_pk,
    cast(game_date as date)                      as game_date,
    inning_topbot,
    cast(estimated_woba_using_speedangle as double) as xwoba,
    cast(woba_value as double)                   as woba_value,
    cast(woba_denom as double)                   as woba_denom,
    cast(launch_speed as double)                 as launch_speed,
    cast(delta_run_exp as double)                as delta_run_exp,
    events,
    description
from read_parquet({{ bronze_path('statcast') }}, union_by_name=true)
