-- Gold: linear weights — the average run value of each plate-appearance outcome,
-- by season. The context-neutral counterpart to RE24: a home run is worth ~1.4
-- runs on average regardless of who was on base when this one was hit.
--
-- Grain: (season, outcome).
--
-- Each weight is the mean of gd_pa_run_value.re24 over every PA with that outcome
-- in complete half-innings -- the population gd_run_expectancy_season was fitted
-- on, so the PA-weighted mean across outcomes is near zero and the weights are
-- already "above average". No constant is added and none is assumed; these are
-- measured from the same matrix that values every other run here. The mean is
-- not exactly zero (-0.0003 to -0.0014 runs per PA, see
-- assert_linear_weights_are_centred for the two reasons); it is left as measured
-- rather than re-centred, because it is the same constant for every player.
--
-- The classic sanity check is built in as a test: HR > 3B > 2B > 1B > BB, and an
-- out is negative. If those ever fail the RE matrix or the state encoding is
-- wrong, not baseball.
{{ config(materialized='table') }}

select
    season,
    outcome,
    count(*)             as plate_appearances,
    avg(re24)            as run_value
from {{ ref('gd_pa_run_value') }}
where season_phase = 'regular'
  and in_complete_half_inning
group by 1, 2
