-- Silver: the team dimension per season — league, division, home venue.
--
-- Grain: (season, team_id).
--
-- Keyed by season rather than pulled once, because divisions are not static: the
-- Astros changed leagues in 2013, clubs relocate, and a projection that assumed a
-- fixed map would seed the bracket wrongly for any historical backtest.
{{ config(materialized='table') }}

select
    cast(season as integer)     as season,
    cast(team_id as integer)    as team_id,
    team_name,
    abbreviation,
    cast(league_id as integer)  as league_id,
    league_name,
    cast(division_id as integer) as division_id,
    division_name,
    cast(venue_id as integer)   as venue_id,
    venue_name
from {{ ref('br_teams') }}
where active
