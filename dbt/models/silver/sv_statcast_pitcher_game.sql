-- Silver: one row per (game_pk, pitcher) — how that pitcher fared in that game,
-- split by times through the order.
--
-- Grain: (game_pk, pitcher).
--
-- **Times through the order (TTO)** is computed exactly, not approximated by
-- PA-count/9: it is the number of times *this specific batter* has already faced
-- *this pitcher* in this game. The /9 approximation drifts as soon as a lineup turns
-- over irregularly (pinch hitters, a pitcher entering mid-inning), which is precisely
-- the situation TTO analysis cares about.
--
-- **xwOBA** uses `estimated_woba_using_speedangle`, which Savant populates on every
-- PA-ending row — not just batted balls. Verified against 2025: strikeout = 0.000,
-- walk = 0.691, field_out = 0.221, home run = 1.299. So it is a true xwOBA including
-- the three true outcomes, and averaging it over PAs needs no special-casing.
--
-- **Starter** is the pitcher who threw the first plate appearance for their side.
{{ config(materialized='table') }}

with spine as (
    select game_pk, game_date, season, season_phase, home_team_id, away_team_id
    from {{ ref('sv_games') }}
),

-- One row per plate appearance (woba_denom > 0 marks the PA-ending pitch).
pa as (
    select
        p.game_pk,
        s.game_date,
        s.season,
        s.season_phase,
        p.pitcher,
        p.batter,
        p.at_bat_number,
        p.xwoba,
        p.events,
        p.delta_run_exp,
        -- The away team bats in the top half, so the HOME team is fielding then.
        case when p.inning_topbot = 'Top' then s.home_team_id else s.away_team_id end as fld_team_id
    from {{ ref('br_statcast') }} p
    join spine s using (game_pk)
    where p.woba_denom > 0
),

with_tto as (
    select
        *,
        row_number() over (
            partition by game_pk, pitcher, batter order by at_bat_number
        ) as tto
    from pa
),

per_pitcher as (
    select
        game_pk,
        any_value(game_date)   as game_date,
        any_value(season)      as season,
        any_value(season_phase) as season_phase,
        pitcher,
        any_value(fld_team_id) as team_id,
        min(at_bat_number)     as first_at_bat,
        count(*)               as batters_faced,
        avg(xwoba)             as xwoba_allowed,
        avg(xwoba) filter (where tto = 1) as xwoba_allowed_tto1,
        avg(xwoba) filter (where tto = 2) as xwoba_allowed_tto2,
        avg(xwoba) filter (where tto >= 3) as xwoba_allowed_tto3plus,
        count(*) filter (where tto = 1)   as bf_tto1,
        count(*) filter (where tto = 2)   as bf_tto2,
        count(*) filter (where tto >= 3)  as bf_tto3plus,
        sum(delta_run_exp)     as run_exp_allowed,
        count(*) filter (where events = 'strikeout') as strikeouts,
        count(*) filter (where events = 'walk')      as walks
    from with_tto
    group by game_pk, pitcher
),

-- The starter is whoever threw their side's first plate appearance.
ranked as (
    select
        *,
        row_number() over (
            partition by game_pk, team_id order by first_at_bat
        ) as appearance_order
    from per_pitcher
)

select
    game_pk,
    game_date,
    season,
    season_phase,
    pitcher,
    team_id,
    appearance_order = 1 as is_starter,
    batters_faced,
    xwoba_allowed,
    xwoba_allowed_tto1,
    xwoba_allowed_tto2,
    xwoba_allowed_tto3plus,
    bf_tto1,
    bf_tto2,
    bf_tto3plus,
    run_exp_allowed,
    strikeouts,
    walks
from ranked
