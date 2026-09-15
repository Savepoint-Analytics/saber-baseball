-- Bronze view over the Lahman People table (CC BY-SA 3.0).
--
-- Biographical facts per player: name, birth date, height/weight, bats/throws,
-- debut. Keyed by bbrefID, which is the join to Chadwick's key_bbref. Lahman
-- releases lag a season, so recent debuts are absent here and silver falls back
-- to Chadwick for the name and to Statcast for handedness.
{{ config(materialized='view') }}

select
    cast("ID" as bigint)              as lahman_row_id,
    "playerID"                        as player_id_lahman,
    "bbrefID"                         as bbref_id,
    "retroID"                         as retro_id,
    "nameFirst"                       as name_first,
    "nameLast"                        as name_last,
    "nameGiven"                       as name_given,
    cast("birthYear" as integer)      as birth_year,
    cast("birthMonth" as integer)     as birth_month,
    cast("birthDay" as integer)       as birth_day,
    "birthCountry"                    as birth_country,
    bats,
    throws,
    cast(height as integer)           as height_in,
    cast(weight as integer)           as weight_lb,
    cast(debut as date)               as mlb_debut,
    cast("finalGame" as date)         as final_game
from read_parquet({{ bronze_path('lahman/table=People') }}, union_by_name=true)
