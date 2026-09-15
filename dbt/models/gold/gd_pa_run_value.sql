-- Gold: the RE24 run value of every plate appearance — what the PA did to the
-- batting team's expected runs, plus the runs it actually scored. The per-PA
-- building block for player RE24, linear weights and the season value marts.
--
-- Grain: (game_pk, at_bat_number), same as sv_plate_appearances.
--
--     re24 = RE(state_after) - RE(state_before) + runs_on_play
--
-- where RE comes from gd_run_expectancy_season for the PA's season (see that
-- model for why season, not era), and RE(state_after) is zero once the third out
-- is recorded. Positive is good for the batter; the pitcher's credit is the
-- negative of the same number, and the two are never stored separately -- they
-- are one quantity with a sign convention.
--
-- **Walk-offs.** When a half-inning ends on a play that is not the third out (a
-- walk-off, a suspended game), sv_plate_appearances coalesces the after-state to
-- three outs and RE(after) is zero. That understates the walk-off hitter's credit
-- by the run expectancy of the state he *would* have left. It is the standard
-- convention (Retrosheet-based RE24 does the same) and it touches one PA per
-- walk-off game; it is documented rather than corrected because the counterfactual
-- state is not observed.
--
-- `in_complete_half_inning` is carried through because it marks the population
-- the RE matrix was fitted on -- the one over which the mean of re24 is zero by
-- construction. gd_linear_weights filters on it so the weights are centred;
-- player totals do not, because a player's walk-off counts.
--
-- Postseason PAs are valued against the regular-season matrix for the same
-- season. The matrix is a property of the run environment, not of the round.
{{ config(materialized='table') }}

with pa as (
    select
        *,
        outs_after * 8
            + (case when b1_after then 1 else 0 end)
            + (case when b2_after then 2 else 0 end)
            + (case when b3_after then 4 else 0 end)            as state_after
    from {{ ref('sv_plate_appearances') }}
),

re as (
    select season, state, run_expectancy
    from {{ ref('gd_run_expectancy_season') }}
)

select
    p.game_pk,
    p.game_date,
    p.season,
    p.season_phase,
    p.venue_id,
    p.at_bat_number,
    p.batter,
    p.pitcher,
    p.bat_team_id,
    p.fld_team_id,
    p.state_before,
    p.state_after,
    p.outcome,
    p.runs_on_play,
    p.woba_value,
    p.woba_denom,
    p.xwoba,
    p.in_complete_half_inning,
    rb.run_expectancy                                           as re_before,
    case when p.outs_after >= 3 then 0.0 else ra.run_expectancy end as re_after,
    case when p.outs_after >= 3 then 0.0 else ra.run_expectancy end
        - rb.run_expectancy + p.runs_on_play                    as re24
from pa p
join re rb on rb.season = p.season and rb.state = p.state_before
left join re ra on ra.season = p.season and ra.state = p.state_after
