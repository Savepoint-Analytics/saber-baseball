-- Bronze view over the raw MLB Stats API schedule Parquet (dbt-duckdb).
-- Read-only passthrough; cleaning/conforming happens in silver.
{{ config(materialized='view') }}

select *
from read_parquet({{ bronze_path('statsapi') }}, union_by_name=true, filename=true)
