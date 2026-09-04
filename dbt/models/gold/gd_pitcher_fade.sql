-- Gold: how much a pitcher slips as his outing lengthens, beyond who he is facing.
--
-- Grain: (era, bf_band).
--
-- The naive version of this measurement is badly confounded. Within a time through
-- the order, "batters faced" tracks *lineup position* -- the first batter a starter
-- sees is the leadoff hitter and the ninth is the worst hitter in the order -- so a
-- raw comparison shows pitchers apparently improving as they go. Subtracting the
-- batter's own season on-base rate removes that, and subtracting the pitcher's own
-- rate removes the fact that better pitchers last longer.
--
-- Residual = actual - (batter rate + pitcher rate - league rate), a linear
-- approximation of the odds-ratio the engine uses. It is coarse but it is measuring a
-- small effect against two large confounds, and the confounds are what matter.
--
-- Beyond ~24 batters the estimate turns down again, which is survivor bias rather
-- than recovery: only a pitcher who is dealing is still out there, and the pitcher's
-- season rate does not capture "he is dealing tonight". Consumers should treat the
-- deep bands as a floor, not a reversal.
{{ config(materialized='table') }}

with league as (
    select avg(case when outcome in ('K', 'OUT') then 0.0 else 1.0 end) as lg
    from {{ ref('sv_plate_appearances') }}
    where season_phase = 'regular'
),

batter_rate as (
    select batter, season,
           avg(case when outcome in ('K', 'OUT') then 0.0 else 1.0 end) as rate
    from {{ ref('sv_plate_appearances') }}
    where season_phase = 'regular'
    group by 1, 2 having count(*) >= 150
),

pitcher_rate as (
    select pitcher, season,
           avg(case when outcome in ('K', 'OUT') then 0.0 else 1.0 end) as rate
    from {{ ref('sv_plate_appearances') }}
    where season_phase = 'regular'
    group by 1, 2 having count(*) >= 150
)

select
    case
        when l.season <= 2018 then '2015-2018'
        when l.season <= 2022 then '2019-2022'
        else '2023-2026'
    end                                             as era,
    -- 6-wide bands. floor() is load-bearing: DuckDB's `/` is float division, so
    -- the obvious integer expression silently produces one band per batter faced.
    least(cast(floor((l.pitcher_bf - 1) / 6) * 6 + 6 as integer), 30) as bf_band,
    count(*)                                        as plate_appearances,
    avg(
        case when l.outcome in ('K', 'OUT') then 0.0 else 1.0 end
        - (b.rate + p.rate - (select lg from league))
    )                                               as residual_obr
from {{ ref('sv_lineup_slots') }} l
join batter_rate b on b.batter = l.batter and b.season = l.season
join pitcher_rate p on p.pitcher = l.pitcher and p.season = l.season
where l.season_phase = 'regular' and l.pitcher_bf <= 30
group by 1, 2
