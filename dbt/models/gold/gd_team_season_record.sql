-- Gold mart: team win/loss + run differential per season *and season phase*.
-- Feeds Pythagorean baselines and is a serving table for dashboards/BI.
--
-- Grain: one row per (season, season_phase, team_id). Splitting on phase rather
-- than pooling matters because the Pythagorean/Elo baselines this feeds must be fit
-- on regular-season play only — postseason is a biased sample (only playoff teams,
-- short series, unrepresentative bullpen usage) and pooling it would quietly bias
-- team strength upward for contenders.
--
-- A team with no 'postseason' row simply missed the playoffs that year, so
-- postseason row counts per season track the format eras: 10 playoff teams through
-- 2019, 16 in the expanded 2020 season, 10 in 2021, and 12 from 2022 on.
--
-- Consumers wanting the traditional standings line filter season_phase = 'regular'.
{{ config(materialized='table') }}

with games as (
    select * from {{ ref('sv_games') }} where home_win is not null
),

home as (
    select season, season_phase, home_team_id as team_id, home_team as team,
           count(*) as g, sum(home_win) as w,
           sum(home_score) as rf, sum(away_score) as ra
    from games group by 1, 2, 3, 4
),

away as (
    select season, season_phase, away_team_id as team_id, away_team as team,
           count(*) as g, sum(1 - home_win) as w,
           sum(away_score) as rf, sum(home_score) as ra
    from games group by 1, 2, 3, 4
),

unioned as (
    select * from home
    union all
    select * from away
)

select
    season,
    season_phase,
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
group by 1, 2, 3
