-- Gold mart: team win/loss + run differential per season from silver games.
-- Feeds Pythagorean baselines and is a serving table for dashboards/BI.
{{ config(materialized='table') }}

with games as (
    select * from {{ ref('sv_games') }} where home_win is not null
),

home as (
    select season, home_team_id as team_id, home_team as team,
           count(*) as g, sum(home_win) as w,
           sum(home_score) as rf, sum(away_score) as ra
    from games group by 1, 2, 3
),

away as (
    select season, away_team_id as team_id, away_team as team,
           count(*) as g, sum(1 - home_win) as w,
           sum(away_score) as rf, sum(home_score) as ra
    from games group by 1, 2, 3
),

unioned as (
    select * from home
    union all
    select * from away
)

select
    season,
    team_id,
    any_value(team)            as team,
    sum(g)                     as games,
    sum(w)                     as wins,
    sum(g) - sum(w)            as losses,
    sum(rf)                    as runs_for,
    sum(ra)                    as runs_against,
    sum(rf) - sum(ra)          as run_diff,
    round(sum(w) * 1.0 / nullif(sum(g), 0), 3) as win_pct
from unioned
group by 1, 2
