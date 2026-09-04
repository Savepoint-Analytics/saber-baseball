-- Gold: how each batter fares against each pitch family, relative to his own level.
--
-- Grain: (season, batter, pitch_family).
--
-- Expressed as a *relative* rate -- his on-base against this family minus his on-base
-- overall -- because the absolute number is dominated by how good a hitter he is,
-- which the PA model already knows. What is new here is the shape of his platoon
-- against stuff.
--
-- **This is a weak signal and is treated as one.** Year-over-year persistence of the
-- relative rate is r ~ +0.16 for fastballs, +0.15 for offspeed and **+0.04 for
-- breaking balls** -- the last is indistinguishable from noise. For scale, team
-- defence repeats at +0.52 to +0.64. So roughly 85% of the observed spread between
-- batters is sampling error, and the shrinkage applied downstream in
-- `src/simulation/pitch_matchup.py` reflects that rather than the raw spread.
--
-- The honest reading is that "he can't hit a curveball" is far less of a stable
-- player trait than broadcast language implies.
{{ config(materialized='table') }}

with by_family as (
    select
        season,
        batter,
        pitch_family,
        count(*)                                                          as pa,
        avg(case when outcome in ('K', 'OUT') then 0.0 else 1.0 end)      as obr
    from {{ ref('sv_plate_appearances') }}
    where season_phase = 'regular' and pitch_family is not null
    group by 1, 2, 3
),

overall as (
    select season, batter, sum(pa) as total_pa,
           sum(obr * pa) / nullif(sum(pa), 0) as overall_obr
    from by_family
    group by 1, 2
)

select
    f.season,
    f.batter,
    f.pitch_family,
    f.pa,
    o.total_pa,
    f.obr,
    o.overall_obr,
    f.obr - o.overall_obr as relative_obr
from by_family f
join overall o on o.season = f.season and o.batter = f.batter
where o.total_pa >= 100
