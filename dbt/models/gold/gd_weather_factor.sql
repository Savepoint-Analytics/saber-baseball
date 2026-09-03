-- Gold: run-environment multiplier from weather, per (era, temperature band, wind, roof).
--
-- Grain: (era, temp_band, wind_out_sign, is_indoor).
--
-- Expressed the same way as gd_park_factor — runs per game in the bucket divided by
-- runs per game league-wide — so the two compose by multiplication into a single
-- per-game run environment.
--
-- The temperature effect is large and physical: a ball carries further in warm air.
-- Measured across 2015-2026 it runs from 8.43 runs per game below 50F to 10.30 above
-- 90F. Wind matters less and mostly when it blows in. A closed roof or dome makes
-- both meaningless, so indoor games form their own bucket and are not banded by a
-- thermostat reading.
--
-- Unlike gd_park_factor this is NOT computed from prior seasons only. A park's
-- dimensions are a fixed property that a lagged window protects against fitting to
-- noise; the physics of a warm evening is not, and is stable across eras. The trade
-- is noted here because it means these factors are descriptive rather than strictly
-- out-of-sample, which is fine for a simulation engine and would not be for a
-- prediction feature.
{{ config(materialized='table') }}

{% set min_games = 150 %}

with games as (
    select
        case
            when g.season <= 2018 then '2015-2018'
            when g.season <= 2022 then '2019-2022'
            else '2023-2026'
        end                                              as era,
        g.home_score + g.away_score                      as runs,
        g.is_indoor,
        coalesce(g.wind_out_sign, 0)                     as wind_out_sign,
        {{ temp_band('g') }}                             as temp_band
    from {{ ref('sv_games') }} g
    where g.season_phase = 'regular' and g.home_score is not null
),

league as (
    select era, avg(runs) as league_rpg from games group by 1
),

bucketed as (
    select
        era,
        temp_band,
        case when is_indoor then 0 else wind_out_sign end as wind_out_sign,
        is_indoor,
        count(*)                                          as games,
        avg(runs)                                         as bucket_rpg
    from games
    group by 1, 2, 3, 4
)

select
    b.era,
    b.temp_band,
    b.wind_out_sign,
    b.is_indoor,
    b.games,
    b.bucket_rpg,
    l.league_rpg,
    -- Thin buckets are pulled toward neutral rather than trusted: a 1.4 factor built
    -- on 60 games is noise, and a simulator would happily amplify it.
    case
        when b.games < {{ min_games }} then 1.0
        else b.bucket_rpg / nullif(l.league_rpg, 0)
    end                                                   as weather_factor
from bucketed b
join league l using (era)
