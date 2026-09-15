-- Gold: the quality of reliever managers actually summon, by score margin.
--
-- Grain: (era, leverage_band).
--
-- The simulator's target. Rather than encoding a rule ("use your closer when
-- ahead by three or fewer"), this measures the outcome of every manager's rules
-- across a decade and lets the engine reproduce the aggregate behaviour.
--
-- `mean_obr_allowed` is the season on-base rate allowed by the pitchers chosen in
-- that band, weighted by appearance. Higher is worse, so the number rises from close
-- games to blowouts.
{{ config(materialized='table') }}

with quality as (
    select
        pitcher,
        season,
        avg(case when outcome in ('K', 'OUT') then 0.0 else 1.0 end) as obr,
        count(*)                                                     as bf
    from {{ ref('sv_plate_appearances') }}
    where season_phase = 'regular'
    group by 1, 2
    having count(*) >= 80
),

joined as (
    select
        case
            when c.season <= 2018 then '2015-2018'
            when c.season <= 2022 then '2019-2022'
            else '2023-2026'
        end             as era,
        c.leverage_band,
        q.obr
    from {{ ref('sv_pitching_changes') }} c
    join quality q on q.pitcher = c.pitcher and q.season = c.season
    where c.season_phase = 'regular'
)

select
    era,
    leverage_band,
    count(*)        as appearances,
    avg(obr)        as mean_obr_allowed
from joined
group by 1, 2
