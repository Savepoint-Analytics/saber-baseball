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
--
-- **WPA** is the same idea on win probability: WP(after) - WP(before) from
-- gd_win_probability, keyed on the season, the inning (shifted for seven-inning
-- games, capped at "extras"), the half, the score difference and the base-out
-- state. Signed for the batting team, so a home batter's WPA is the change in
-- the home side's win probability and an away batter's is the change in the
-- away side's. **leverage** is the leverage index of the situation the PA
-- started in (league mean 1.0).
--
-- **"After" is the next plate appearance as observed**, in whatever inning, half,
-- score and base-out state it actually began -- the same convention the
-- after-state already uses for the bases. It is not the situation the rules
-- say should follow: postseason extra innings start with the bases empty while
-- the season's table assumes the ghost runner, and a handful of half-innings
-- open with a runner on third. Reading the real next PA makes WPA telescope
-- exactly across a game, which `assert_wpa_telescopes_to_the_result` checks.
-- Only a game's final PA is scored against state 24, the value once the half is
-- over: 1 for a walk-off or a top-of-the-ninth close-out, 0 for a loss on the
-- final out.
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
),

wp as (
    select season, inning, half, score_diff, state, win_prob, leverage
    from {{ ref('gd_win_probability') }}
),

-- The next PA in the game, whichever half-inning it falls in.
following as (
    select
        *,
        lead(inning)            over g as next_inning,
        lead(inning_topbot)     over g as next_half,
        lead(home_score_before) over g as next_home_score,
        lead(away_score_before) over g as next_away_score,
        lead(state_before)      over g as next_state
    from pa
    window g as (partition by game_pk order by at_bat_number)
),

-- The situation before and after each PA, in the table's coordinates.
situated as (
    select
        p.*,
        least(p.inning + 9 - p.regulation_innings, 10)              as wp_inning,
        p.inning_topbot                                              as wp_half,
        p.inning_topbot = 'Bot'                                      as home_batting,
        greatest(-20, least(20, p.home_score_before - p.away_score_before))
                                                                     as diff_before,
        case
            when p.next_inning is null then least(p.inning + 9 - p.regulation_innings, 10)
            else least(p.next_inning + 9 - p.regulation_innings, 10)
        end                                                          as wp_inning_after,
        coalesce(p.next_half, p.inning_topbot)                       as wp_half_after,
        greatest(-20, least(20, case
            when p.next_inning is null then
                p.home_score_before - p.away_score_before
                + case when p.inning_topbot = 'Bot' then p.runs_on_play else -p.runs_on_play end
            else p.next_home_score - p.next_away_score
        end))                                                        as diff_after,
        coalesce(p.next_state, 24)                                   as wp_state_after
    from following p
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
    p.home_batting,
    p.diff_before                                               as home_score_diff_before,
    rb.run_expectancy                                           as re_before,
    case when p.outs_after >= 3 then 0.0 else ra.run_expectancy end as re_after,
    case when p.outs_after >= 3 then 0.0 else ra.run_expectancy end
        - rb.run_expectancy + p.runs_on_play                    as re24,
    wb.win_prob                                                 as home_wp_before,
    wa.win_prob                                                 as home_wp_after,
    (wa.win_prob - wb.win_prob) * case when p.home_batting then 1 else -1 end
                                                                as wpa,
    wb.leverage                                                 as leverage
from situated p
join re rb on rb.season = p.season and rb.state = p.state_before
left join re ra on ra.season = p.season and ra.state = p.state_after
join wp wb
  on wb.season = p.season and wb.inning = p.wp_inning and wb.half = p.wp_half
 and wb.score_diff = p.diff_before and wb.state = p.state_before
join wp wa
  on wa.season = p.season and wa.inning = p.wp_inning_after and wa.half = p.wp_half_after
 and wa.score_diff = p.diff_after and wa.state = p.wp_state_after
