-- Silver: the FULL schedule, played and unplayed.
--
-- Grain: (game_pk).
--
-- `sv_games` deliberately keeps only completed games — it is the spine models train
-- on, and a half-finished game has no outcome to learn from. But a season projection
-- needs the opposite: the games that have *not* happened yet are the ones being
-- Monte-Carloed. As of 2026-09-04 that is 399 scheduled games the training spine
-- cannot see.
--
-- Both views are needed and they are different things, so they are separate models
-- rather than one filtered two ways at every call site.
--
-- Postponed games are excluded from `is_remaining`: they will be replayed under a new
-- game_pk on a new date, so counting them would double-count the makeup.
{{ config(materialized='table') }}

with src as (
    select *
    from {{ ref('br_statsapi_schedule') }}
    where game_type = 'R'
),

ranked as (
    select
        src.*,
        row_number() over (
            partition by game_pk order by cast(game_date as date) desc
        ) as _rank
    from src
)

select
    cast(game_pk as bigint)                          as game_pk,
    cast(game_date as date)                          as game_date,
    cast(season as integer)                          as season,
    status,
    cast(home_team_id as integer)                    as home_team_id,
    home_team,
    cast(away_team_id as integer)                    as away_team_id,
    away_team,
    cast(venue_id as integer)                        as venue_id,
    cast(home_score as integer)                      as home_score,
    cast(away_score as integer)                      as away_score,
    status in ('Final', 'Completed Early')           as is_played,
    case
        when home_score is null or away_score is null then null
        when home_score > away_score then 1 else 0
    end                                              as home_win,
    -- Anything still to be played and still on the calendar.
    status not in ('Final', 'Completed Early', 'Postponed', 'Cancelled') as is_remaining
from ranked
where _rank = 1
