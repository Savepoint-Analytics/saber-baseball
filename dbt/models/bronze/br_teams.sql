-- Bronze view over the MLB team dimension (league, division, venue).
{{ config(materialized='view') }}

select *
from read_parquet({{ bronze_path('teams') }}, union_by_name=true)
