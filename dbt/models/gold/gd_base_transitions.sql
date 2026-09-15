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

-- Only complete half-innings: see `in_complete_half_inning` in sv_plate_appearances
-- for why a walk-off cannot be detected by counting outs, and why the game's final
-- half-inning is the one that is dropped. It costs ~6% of half-innings and buys a
-- matrix that cannot contain impossible transitions.
with complete as (
    select *, {{ era('season') }} as era
    from {{ ref('sv_plate_appearances') }}
    where season_phase = 'regular'
      and in_complete_half_inning
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
