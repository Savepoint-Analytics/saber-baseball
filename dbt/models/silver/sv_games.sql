-- Silver: cleaned, conformed game-level table from the StatsAPI schedule.
-- One row per completed game with typed scores and a derived home-win flag —
-- the spine for Phase-3 outcome modeling.
{{ config(materialized='table') }}

with src as (
    select * from {{ ref('br_statsapi_schedule') }}
)

select
    cast(game_pk as bigint)                      as game_pk,
    cast(game_date as date)                      as game_date,
    season,
    game_type,
    status,
    home_team_id,
    home_team,
    away_team_id,
    away_team,
    cast(home_score as integer)                  as home_score,
    cast(away_score as integer)                  as away_score,
    case
        when home_score is null or away_score is null then null
        when home_score > away_score then 1 else 0
    end                                          as home_win,
    venue
from src
where status = 'Final'
