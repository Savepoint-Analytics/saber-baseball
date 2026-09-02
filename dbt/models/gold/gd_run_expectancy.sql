-- Gold: the RE24 run-expectancy matrix — expected runs scored from each base-out
-- state through the end of the half-inning. Phase 2's core artifact, and the thing
-- that validates the transition data the simulation engine is built on.
--
-- Grain: (season_bucket, state) — 24 states per bucket.
--
-- The final half-inning of each game is excluded. A walk-off stops play the moment
-- the run scores rather than at three outs, so those half-innings are truncated by
-- rule and would drag every state's expectancy downward. See the note on the
-- `last_half` CTE for why they cannot be detected by counting outs.
--
-- Bucketed by era rather than pooled across 2015-2026, because run environment moves
-- (the 2019 ball, the 2023 rule changes) and a single pooled matrix would misprice
-- both ends of the range.
{{ config(materialized='table') }}

with pa as (
    select *
    from {{ ref('sv_plate_appearances') }}
    where season_phase = 'regular'
),

-- A half-inning is only known to be complete if it is not the game's last. The
-- outs_after column coalesces a missing "next PA" to 3, so max(outs_after) is 3 for
-- every half-inning and cannot distinguish a real third out from the end of play --
-- which is why the earlier filter silently excluded nothing, and why 1.25% of
-- bases-loaded home runs appeared to end the inning. Only the final half-inning of a
-- game can be truncated (a walk-off stops play mid-inning), so dropping exactly that
-- one is unambiguous. It costs ~6% of half-innings and buys a matrix that cannot
-- contain impossible transitions.
last_half as (
    select
        game_pk,
        max(inning * 2 + case when inning_topbot = 'Bot' then 1 else 0 end) as last_half_id
    from pa
    group by 1
),

complete as (
    select p.*
    from pa p
    join last_half l on l.game_pk = p.game_pk
    where p.inning * 2 + case when p.inning_topbot = 'Bot' then 1 else 0 end < l.last_half_id
),

with_rest as (
    select
        *,
        sum(runs_on_play) over (
            partition by game_pk, inning, inning_topbot
            order by at_bat_number
            rows between current row and unbounded following
        ) as runs_to_end_of_inning
    from complete
)

select
    case
        when season <= 2018 then '2015-2018'
        when season <= 2022 then '2019-2022'
        else '2023-2026'
    end                                  as era,
    state_before                         as state,
    outs_before                          as outs,
    b1_before                            as on_1b,
    b2_before                            as on_2b,
    b3_before                            as on_3b,
    count(*)                             as plate_appearances,
    avg(runs_to_end_of_inning)           as run_expectancy
from with_rest
group by 1, 2, 3, 4, 5, 6
