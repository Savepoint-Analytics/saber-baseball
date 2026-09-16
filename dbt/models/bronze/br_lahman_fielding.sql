-- Bronze view over the Lahman Fielding table (CC BY-SA 3.0).
--
-- One row per (player, season, stint, position) with games played there. Used
-- only to derive a primary position for the player dimension; the fielding
-- counting stats are carried for later defensive work. Outfielders are recorded
-- as a single 'OF' position in the modern rows, not LF/CF/RF.
{{ config(materialized='view') }}

select
    "playerID"                        as player_id_lahman,
    cast("yearID" as integer)         as season,
    cast(stint as integer)            as stint,
    "teamID"                          as team_id_lahman,
    "POS"                             as position,
    cast("G" as integer)              as games,
    cast("GS" as integer)             as games_started,
    cast("InnOuts" as integer)        as inn_outs,
    cast("PO" as integer)             as putouts,
    cast("A" as integer)              as assists,
    cast("E" as integer)              as errors,
    cast("DP" as integer)             as double_plays
from read_parquet({{ bronze_path('lahman/table=Fielding') }}, union_by_name=true)
