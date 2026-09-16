-- Bronze view over the Chadwick Bureau player register (open data).
--
-- The ID crosswalk between MLBAM (what Statcast and the Stats API key on),
-- Retrosheet, Baseball-Reference/Lahman and FanGraphs. The register uses -1 as a
-- sentinel for "no such id"; it is passed through here and nulled in silver, so
-- bronze stays a faithful read of the source.
{{ config(materialized='view') }}

select
    cast(key_mlbam as bigint)          as key_mlbam,
    key_retro,
    key_bbref,
    cast(key_fangraphs as bigint)      as key_fangraphs,
    name_first,
    name_last,
    cast(mlb_played_first as integer)  as mlb_played_first,
    cast(mlb_played_last as integer)   as mlb_played_last
from read_parquet({{ bronze_path('chadwick') }}, union_by_name=true)
