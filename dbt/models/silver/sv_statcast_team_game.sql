-- Silver: one row per (game_pk, team_id) summarising how that team hit and pitched
-- in that game, from pitch-level Statcast.
--
-- Grain: (game_pk, team_id) — two rows per game.
--
-- Joined to the spine on **game_pk, never on date**. Statcast files a suspended game
-- under its original date while sv_games dates it by completion, so a date join
-- silently drops those games (see docs/methodology.md).
--
-- Batting side comes from `inning_topbot`: the away team bats in the top half, the
-- home team in the bottom. That is what lets us attribute each pitch to a team
-- without a team-abbreviation crosswalk.
--
-- `delta_run_exp` is signed from the batting team's perspective (positive = the
-- offence gained expected runs), so the same column serves as offence-created when
-- grouped by the batting team and defence-allowed when grouped by the fielding team.
{{ config(materialized='table') }}

with spine as (
    select game_pk, game_date, season, season_phase, home_team_id, away_team_id
    from {{ ref('sv_games') }}
),

tagged as (
    select
        s.game_pk,
        s.game_date,
        s.season,
        s.season_phase,
        case when p.inning_topbot = 'Top' then s.away_team_id else s.home_team_id end as bat_team_id,
        case when p.inning_topbot = 'Top' then s.home_team_id else s.away_team_id end as fld_team_id,
        p.xwoba,
        p.woba_value,
        p.woba_denom,
        p.launch_speed,
        p.delta_run_exp,
        p.events
    from {{ ref('br_statcast') }} p
    join spine s using (game_pk)
),

offense as (
    select
        game_pk, game_date, season, season_phase,
        bat_team_id                                            as team_id,
        avg(xwoba)                                             as off_xwoba,
        sum(woba_value) / nullif(sum(woba_denom), 0)           as off_woba,
        sum(delta_run_exp)                                     as off_run_exp,
        avg(case when launch_speed is null then null
                 when launch_speed >= 95 then 1.0 else 0.0 end) as off_hard_hit_rate,
        sum(case when events = 'strikeout' then 1 else 0 end)  as off_strikeouts,
        sum(case when events = 'walk' then 1 else 0 end)       as off_walks,
        sum(case when woba_denom > 0 then 1 else 0 end)        as off_pa
    from tagged
    group by 1, 2, 3, 4, 5
),

defense as (
    select
        game_pk,
        fld_team_id                                            as team_id,
        avg(xwoba)                                             as def_xwoba,
        sum(woba_value) / nullif(sum(woba_denom), 0)           as def_woba,
        sum(delta_run_exp)                                     as def_run_exp,
        avg(case when launch_speed is null then null
                 when launch_speed >= 95 then 1.0 else 0.0 end) as def_hard_hit_rate,
        sum(case when events = 'strikeout' then 1 else 0 end)  as def_strikeouts,
        sum(case when woba_denom > 0 then 1 else 0 end)        as def_pa
    from tagged
    group by 1, 2
)

select
    o.game_pk,
    o.game_date,
    o.season,
    o.season_phase,
    o.team_id,
    o.off_xwoba,
    o.off_woba,
    o.off_run_exp,
    o.off_hard_hit_rate,
    o.off_strikeouts,
    o.off_walks,
    o.off_pa,
    d.def_xwoba,
    d.def_woba,
    d.def_run_exp,
    d.def_hard_hit_rate,
    d.def_strikeouts,
    d.def_pa,
    -- Net expected-run contribution in this game: what the offence created minus
    -- what the defence allowed. The single best one-number summary of the game.
    o.off_run_exp - d.def_run_exp as net_run_exp
from offense o
join defense d
  on o.game_pk = d.game_pk and o.team_id = d.team_id
