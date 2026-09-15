-- Silver: one row per plate appearance with the base-out state before and after,
-- and the runs it produced. The foundation for run expectancy (Phase 2) and for the
-- plate-appearance simulation engine (Phases 4-5).
--
-- Grain: (game_pk, at_bat_number).
--
-- **A PA is a row where `events` is populated**, not where `woba_denom > 0`. Statcast
-- writes `events` on the last pitch of every plate appearance; `woba_denom` is 0 for
-- sacrifices and catcher's interference, so filtering on it silently drops ~1% of
-- PAs — and drops them non-randomly, precisely the bunts and sacrifices that move
-- runners. A transition matrix built that way would understate advancement.
--
-- **Outcome classes** collapse 22 raw event types to 9. Reaching on error is kept
-- separate from outs rather than folded in: the batter reaches base, so folding it
-- into OUT would teach the transition matrix that outs sometimes fail to record an
-- out, which is a corruption of the thing the matrix exists to encode.
--
-- **Runs are measured across the whole plate appearance**, not off its final pitch.
-- `bat_score` on the last pitch already includes any run that scored earlier in the
-- same PA on a wild pitch, passed ball, balk or steal of home, so
-- `post_bat_score - bat_score` on that row silently drops those runs. It cost 0.135
-- runs per game (304 of 2,430 games in 2025 failed to reconcile with the final
-- score), all in the same direction, and it biased run expectancy downward. Taking
-- min(bat_score) to max(post_bat_score) over every pitch of the PA captures them.
--
-- **The "after" state** is read from the *next* PA of the same half-inning. When
-- there is no next PA the half-inning ended, so three outs are recorded and the bases
-- are cleared. That is how the matrix learns advancement empirically — a single with
-- a runner on second scores him some fraction of the time, and the data says which.
{{ config(materialized='table') }}

with spine as (
    select game_pk, game_date, season, season_phase, home_team_id, away_team_id
    from {{ ref('sv_games') }}
),

-- Score at the first pitch of each PA to the score after its last. The score is
-- monotonic within a PA, so min/max are exactly the endpoints.
pa_runs as (
    select
        game_pk,
        at_bat_number,
        max(post_bat_score) - min(bat_score) as runs_in_pa
    from {{ ref('br_statcast') }}
    group by 1, 2
),

pa as (
    select
        p.game_pk,
        s.game_date,
        s.season,
        s.season_phase,
        p.inning,
        p.inning_topbot,
        p.at_bat_number,
        p.batter,
        p.pitcher,
        p.stand,
        p.p_throws,
        case when p.inning_topbot = 'Top' then s.away_team_id else s.home_team_id end as bat_team_id,
        case when p.inning_topbot = 'Top' then s.home_team_id else s.away_team_id end as fld_team_id,
        p.outs_when_up                             as outs_before,
        p.on_1b is not null                        as b1_before,
        p.on_2b is not null                        as b2_before,
        p.on_3b is not null                        as b3_before,
        coalesce(r.runs_in_pa, 0)                  as runs_on_play,
        p.events,
        -- Pitch family of the pitch that ENDED the plate appearance. Grouped into
        -- three families rather than kept as 18 raw types: 4,181 batter x pitch-type
        -- pairs have under 20 plate appearances in a season, so the raw grain is
        -- almost entirely noise. Families are the standard split and keep enough
        -- sample per batter to estimate anything.
        case
            when p.pitch_type in ('FF', 'SI', 'FC', 'FA') then 'fastball'
            when p.pitch_type in ('SL', 'ST', 'CU', 'KC', 'SV', 'CS', 'SC') then 'breaking'
            when p.pitch_type in ('CH', 'FS', 'FO', 'EP') then 'offspeed'
        end                                        as pitch_family,
        case
            when p.events in ('strikeout', 'strikeout_double_play')          then 'K'
            when p.events in ('walk', 'intent_walk')                          then 'BB'
            when p.events = 'hit_by_pitch'                                    then 'HBP'
            when p.events = 'single'                                          then '1B'
            when p.events = 'double'                                          then '2B'
            when p.events = 'triple'                                          then '3B'
            when p.events = 'home_run'                                        then 'HR'
            when p.events = 'field_error'                                     then 'ROE'
            else 'OUT'
        end                                        as outcome
    from {{ ref('br_statcast') }} p
    join spine s using (game_pk)
    left join pa_runs r
      on r.game_pk = p.game_pk and r.at_bat_number = p.at_bat_number
    where p.events is not null
),

sequenced as (
    select
        *,
        lead(outs_before) over w  as next_outs,
        lead(b1_before)   over w  as next_b1,
        lead(b2_before)   over w  as next_b2,
        lead(b3_before)   over w  as next_b3
    from pa
    window w as (partition by game_pk, inning, inning_topbot order by at_bat_number)
)

select
    game_pk,
    game_date,
    season,
    season_phase,
    inning,
    inning_topbot,
    at_bat_number,
    batter,
    pitcher,
    stand,
    p_throws,
    bat_team_id,
    fld_team_id,
    outs_before,
    b1_before,
    b2_before,
    b3_before,
    outcome,
    events,
    pitch_family,
    runs_on_play,
    next_outs is null                              as ends_half_inning,
    coalesce(next_outs, 3)                         as outs_after,
    coalesce(next_b1, false)                       as b1_after,
    coalesce(next_b2, false)                       as b2_after,
    coalesce(next_b3, false)                       as b3_after,
    -- Canonical 0-23 base-out state id: outs * 8 + a 3-bit base occupancy code.
    -- One integer instead of four columns makes the transition matrix a plain
    -- lookup rather than a four-way join.
    outs_before * 8
        + (case when b1_before then 1 else 0 end)
        + (case when b2_before then 2 else 0 end)
        + (case when b3_before then 4 else 0 end) as state_before
from sequenced
