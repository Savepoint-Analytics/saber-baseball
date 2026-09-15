-- Gold: what each pitcher actually throws, as a share of his plate appearances.
--
-- Grain: (season, pitcher, pitch_family).
--
-- Measured on PA-ending pitches rather than all pitches, deliberately. The engine
-- uses this to weight a batter's family profile into a matchup, and what matters
-- there is the family a plate appearance is likely to *finish* on -- a pitcher who
-- shows a slider early and finishes with the fastball is a fastball matchup, whatever
-- his overall pitch counts say.
{{ config(materialized='table') }}

with counted as (
    select
        season,
        pitcher,
        pitch_family,
        count(*) as pa
    from {{ ref('sv_plate_appearances') }}
    where season_phase = 'regular' and pitch_family is not null
    group by 1, 2, 3
)

select
    season,
    pitcher,
    pitch_family,
    pa,
    sum(pa) over (partition by season, pitcher)                    as total_pa,
    pa::double / sum(pa) over (partition by season, pitcher)        as share
from counted
qualify sum(pa) over (partition by season, pitcher) >= 100
