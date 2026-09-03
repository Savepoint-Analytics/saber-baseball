-- Gold: how a richer run environment redistributes plate-appearance outcomes.
--
-- Grain: (era, outcome).
--
-- A park factor says a venue yields 27% more runs; it does not say *how*. This model
-- measures that: comparing games in high (>= 1.05) and low (<= 0.95) run environments
-- gives the direction in which outcome rates move, and the simulator then scales that
-- direction to hit whatever multiplier a given park and night call for.
--
-- The measured direction is the physical one — a ball that carries turns strikeouts
-- into extra-base hits. Triples move most (+0.20 in log rate), then home runs (+0.15)
-- and doubles (+0.11), while strikeouts fall (-0.06). Deriving it rather than
-- assuming it matters because an assumed direction (say, "scale all hits equally")
-- would produce the right mean runs with the wrong distribution, and distribution
-- shape is the entire reason this is being wired in.
--
-- Environment here is park x weather, the same product the engine applies.
{{ config(materialized='table') }}

with ctx as (
    select
        g.game_pk,
        case
            when g.season <= 2018 then '2015-2018'
            when g.season <= 2022 then '2019-2022'
            else '2023-2026'
        end as era,
        coalesce(pf.park_factor, 1.0) * coalesce(wf.weather_factor, 1.0) as env
    from {{ ref('sv_games') }} g
    left join {{ ref('gd_park_factor') }} pf
      on pf.season = g.season and pf.venue_id = g.venue_id
    left join {{ ref('gd_weather_factor') }} wf
      on wf.era = case
            when g.season <= 2018 then '2015-2018'
            when g.season <= 2022 then '2019-2022'
            else '2023-2026' end
     and wf.is_indoor = g.is_indoor
     and wf.wind_out_sign = case when g.is_indoor then 0 else coalesce(g.wind_out_sign, 0) end
     and wf.temp_band = {{ temp_band('g') }}
    where g.season_phase = 'regular' and g.home_score is not null
),

banded as (
    select
        c.era,
        case when c.env >= 1.05 then 'high' when c.env <= 0.95 then 'low' else 'mid' end as band,
        p.outcome,
        count(*) as n
    from {{ ref('sv_plate_appearances') }} p
    join ctx c using (game_pk)
    group by 1, 2, 3
),

rates as (
    select era, band, outcome,
           n::double / sum(n) over (partition by era, band) as rate
    from banded
)

select
    h.era,
    h.outcome,
    l.rate                       as low_env_rate,
    h.rate                       as high_env_rate,
    ln(h.rate / nullif(l.rate, 0)) as log_ratio
from rates h
join rates l on l.era = h.era and l.outcome = h.outcome and l.band = 'low'
where h.band = 'high'
