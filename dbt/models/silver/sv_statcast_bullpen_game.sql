-- Silver: one row per (game_pk, team_id) summarising that team's *relief* pitching.
--
-- Grain: (game_pk, team_id). Teams whose starter went the distance have no relievers
-- and so no row — consumers must treat that as "bullpen unused", not missing data.
--
-- Relief xwOBA is weighted by batters faced, not a flat mean over pitchers. A flat
-- mean gives a mop-up man who faced one batter the same weight as a setup man who
-- faced seven, which is exactly backwards for measuring how well a bullpen threw.
{{ config(materialized='table') }}

select
    game_pk,
    any_value(game_date)                                     as game_date,
    any_value(season)                                        as season,
    any_value(season_phase)                                  as season_phase,
    team_id,
    count(*)                                                 as relievers_used,
    sum(batters_faced)                                       as relief_bf,
    sum(xwoba_allowed * batters_faced) / nullif(sum(batters_faced), 0) as relief_xwoba_allowed,
    sum(run_exp_allowed)                                     as relief_run_exp_allowed,
    sum(strikeouts)                                          as relief_strikeouts,
    sum(walks)                                               as relief_walks
from {{ ref('sv_statcast_pitcher_game') }}
where not is_starter
group by game_pk, team_id
