-- Gold: empirical base-out transition matrix — P(next state, runs | state, outcome).
--
-- Grain: (era, state_before, outcome, state_after, runs_on_play).
--
-- This is the piece that makes a PA simulation possible without hand-written
-- baserunning rules. A single with a runner on second does not determine whether he
-- scores; the data says he does some fraction of the time, and that fraction differs
-- by out count (a runner goes on contact with two out). Encoding those rules by hand
-- would be guesswork; here they are measured.
--
-- `state_after = 24` is the absorbing "three outs, half-inning over" state. Keeping
-- it in the same integer space as the 24 live states lets the simulator step the
-- chain with one lookup and no branching.
--
-- Walk-off half-innings are excluded for the same reason as in gd_run_expectancy:
-- play stops on the winning run rather than at three outs, so their transitions are
-- truncated by rule rather than by baseball.
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
    select p.*,
        case
            when p.season <= 2018 then '2015-2018'
            when p.season <= 2022 then '2019-2022'
            else '2023-2026'
        end as era
    from pa p
    join last_half l on l.game_pk = p.game_pk
    where p.inning * 2 + case when p.inning_topbot = 'Bot' then 1 else 0 end < l.last_half_id
),

counted as (
    select
        era,
        state_before,
        outcome,
        case
            when outs_after >= 3 then 24
            else outs_after * 8
                 + (case when b1_after then 1 else 0 end)
                 + (case when b2_after then 2 else 0 end)
                 + (case when b3_after then 4 else 0 end)
        end                              as state_after,
        runs_on_play,
        count(*)                         as n
    from complete
    group by 1, 2, 3, 4, 5
)

select
    era,
    state_before,
    outcome,
    state_after,
    runs_on_play,
    n,
    n::double / sum(n) over (partition by era, state_before, outcome) as prob
from counted
