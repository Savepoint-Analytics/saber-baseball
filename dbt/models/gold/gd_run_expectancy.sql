-- Gold: the RE24 run-expectancy matrix — expected runs scored from each base-out
-- state through the end of the half-inning. Phase 2's core artifact, and the thing
-- that validates the transition data the simulation engine is built on.
--
-- Grain: (season_bucket, state) — 24 states per bucket.
--
-- The final half-inning of each game is excluded. A walk-off stops play the moment
-- the run scores rather than at three outs, so those half-innings are truncated by
-- rule and would drag every state's expectancy downward. See the note on
-- `in_complete_half_inning` in sv_plate_appearances for why they cannot be
-- detected by counting outs.
--
-- Bucketed by era rather than pooled across 2015-2026, because run environment moves
-- (the 2019 ball, the 2023 rule changes) and a single pooled matrix would misprice
-- both ends of the range.
{{ config(materialized='table') }}

with with_rest as (
    select *
    from {{ ref('sv_plate_appearances') }}
    where season_phase = 'regular'
      and in_complete_half_inning
)

select
    {{ era('season') }}                  as era,
    state_before                         as state,
    outs_before                          as outs,
    b1_before                            as on_1b,
    b2_before                            as on_2b,
    b3_before                            as on_3b,
    count(*)                             as plate_appearances,
    avg(runs_to_end_of_inning)           as run_expectancy
from with_rest
group by 1, 2, 3, 4, 5, 6
