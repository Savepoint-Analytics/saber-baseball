-- Gold: rolling bullpen quality and recent workload per team, as known before a game.
--
-- Grain: (game_pk, team_id).
--
-- Two distinct things live here and they answer different questions:
--
--   * **Quality** — how well the bullpen has thrown over its last 30 appearances.
--   * **Fatigue** — how many batters it faced in the last 3 games. A good bullpen
--     that threw 14 batters yesterday is not the same asset tonight, and this is the
--     part team-level form cannot express at all: it is a *state*, not a skill.
--
-- CAUSALITY: every window is `rows between N preceding and 1 preceding`, excluding
-- the current game. Same rule as gd_team_form and gd_starter_form.
--
-- The frame is built off sv_statcast_team_game rather than the bullpen table so that
-- games in which the bullpen was never used still produce a row. Joining from the
-- bullpen side would silently drop complete-game starts from the window and quietly
-- overstate recent workload.
{{ config(materialized='table') }}

{% set window = 30 %}
{% set fatigue_window = 3 %}

with frame as (
    select t.game_pk, t.game_date, t.season, t.season_phase, t.team_id,
           coalesce(b.relief_bf, 0)          as relief_bf,
           b.relief_xwoba_allowed            as relief_xwoba_allowed,
           coalesce(b.relief_run_exp_allowed, 0) as relief_run_exp_allowed,
           coalesce(b.relievers_used, 0)     as relievers_used
    from {{ ref('sv_statcast_team_game') }} t
    left join {{ ref('sv_statcast_bullpen_game') }} b
      on b.game_pk = t.game_pk and b.team_id = t.team_id
)

select
    game_pk,
    game_date,
    season,
    season_phase,
    team_id,
    {% for col in ['relief_xwoba_allowed', 'relief_run_exp_allowed', 'relief_bf', 'relievers_used'] %}
    avg({{ col }}) over (
        partition by team_id order by game_date, game_pk
        rows between {{ window }} preceding and 1 preceding
    ) as bp_form_{{ col }},
    {% endfor %}
    -- Fatigue: batters faced by the bullpen across the previous {{ fatigue_window }}
    -- games. Summed, not averaged — the burden is cumulative.
    sum(relief_bf) over (
        partition by team_id order by game_date, game_pk
        rows between {{ fatigue_window }} preceding and 1 preceding
    ) as bp_recent_bf,
    count(*) over (
        partition by team_id order by game_date, game_pk
        rows between {{ window }} preceding and 1 preceding
    ) as bp_games_in_window
from frame
