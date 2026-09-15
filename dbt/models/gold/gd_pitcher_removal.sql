-- Gold: the empirical hazard of a pitcher being removed after a plate appearance.
--
-- Grain: (era, is_starter, times_through_order, bf_bucket).
--
-- This is the substitution policy the simulator draws from. It is measured rather
-- than assumed because a hand-written rule ("pull the starter after 100 pitches")
-- encodes a guess about managerial behaviour, and managerial behaviour is exactly
-- what changes between eras — the third-time-through hook has tightened sharply
-- since 2015.
--
-- Bucketing batters faced rather than using it raw keeps each cell large enough to
-- estimate. Beyond 30 batters faced the sample thins out, so those collapse into a
-- single terminal bucket.
{{ config(materialized='table') }}

with pa as (
    select
        case
            when season <= 2018 then '2015-2018'
            when season <= 2022 then '2019-2022'
            else '2023-2026'
        end                                                    as era,
        pitcher_bf = 1 and is_first_pa_of_game                 as unused_flag,
        times_through_order,
        pitcher_bf,
        removed_after,
        game_pk,
        fld_team_id,
        pitcher
    from {{ ref('sv_lineup_slots') }}
    where season_phase = 'regular'
),

-- A pitcher is a starter for this game if he threw its first plate appearance.
starters as (
    select distinct game_pk, fld_team_id, pitcher
    from {{ ref('sv_lineup_slots') }}
    where is_first_pa_of_game
),

tagged as (
    select
        p.era,
        exists (
            select 1 from starters s
            where s.game_pk = p.game_pk and s.fld_team_id = p.fld_team_id
              and s.pitcher = p.pitcher
        )                                                      as is_starter,
        least(p.times_through_order, 4)                        as tto,
        case
            when p.pitcher_bf <= 6  then 6
            when p.pitcher_bf <= 12 then 12
            when p.pitcher_bf <= 18 then 18
            when p.pitcher_bf <= 24 then 24
            when p.pitcher_bf <= 30 then 30
            else 99
        end                                                    as bf_bucket,
        p.removed_after
    from pa p
)

select
    era,
    is_starter,
    tto,
    bf_bucket,
    count(*)                                                   as plate_appearances,
    avg(case when removed_after then 1.0 else 0.0 end)         as removal_hazard
from tagged
group by 1, 2, 3, 4
having count(*) >= 50
