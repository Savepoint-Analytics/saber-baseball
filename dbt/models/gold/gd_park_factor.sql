-- Gold: run-scoring park factor per (season, venue), computed from PRIOR seasons only.
--
-- Grain: (season, venue_id).
--
-- A park factor fitted on the season it is applied to would be circular — the runs it
-- is built from are the runs being predicted. The window is therefore the three
-- seasons *before* the target season, which is also long enough to damp the noise in
-- a single park's ~81 home games.
--
-- Definition: runs per game at this venue over the window, divided by runs per game
-- league-wide over the same window. 1.0 is neutral; Coors sits well above, Petco
-- below. Both teams are affected equally, so this is not on its own a win-probability
-- feature — it earns its place only if run environment interacts with how teams are
-- built, which is exactly what the backtest is there to decide.
--
-- Venues with fewer than 200 window games (a new park, a one-off neutral site) fall
-- back to 1.0 rather than reporting a factor built on a handful of games.
{{ config(materialized='table') }}

{% set lookback = 3 %}
{% set min_games = 200 %}

with game_runs as (
    select season, venue_id, home_score + away_score as runs
    from {{ ref('sv_games') }}
    where season_phase = 'regular' and home_score is not null and venue_id is not null
),

seasons as (select distinct season from game_runs),

venue_window as (
    select
        s.season                       as target_season,
        g.venue_id,
        count(*)                       as window_games,
        avg(g.runs)                    as venue_rpg
    from seasons s
    join game_runs g
      on g.season between s.season - {{ lookback }} and s.season - 1
    group by 1, 2
),

league_window as (
    select
        s.season   as target_season,
        avg(g.runs) as league_rpg
    from seasons s
    join game_runs g
      on g.season between s.season - {{ lookback }} and s.season - 1
    group by 1
)

select
    v.target_season                    as season,
    v.venue_id,
    v.window_games,
    v.venue_rpg,
    l.league_rpg,
    case
        when v.window_games < {{ min_games }} then 1.0
        else v.venue_rpg / nullif(l.league_rpg, 0)
    end                                as park_factor
from venue_window v
join league_window l using (target_season)
