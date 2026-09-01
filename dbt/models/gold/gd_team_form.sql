-- Gold: rolling Statcast form per team, as known *before* each game.
--
-- Grain: (game_pk, team_id) — the feature row a model uses to predict that game.
--
-- CAUSALITY IS THE WHOLE POINT OF THIS MODEL. Every window is
-- `rows between N preceding and 1 preceding`, which excludes the current row. If it
-- were `between N preceding and current row`, each game's features would contain
-- that game's own result and the backtest would report a score that no live
-- prediction could ever reproduce. That off-by-one is invisible in the output —
-- the numbers still look plausible — so it is pinned by a dbt test below and by
-- tests/test_features.py.
--
-- Windows are ordered by (game_date, game_pk); game_pk breaks the doubleheader tie
-- so the ordering is fully determined by the data rather than by scan order.
--
-- Form deliberately spans season boundaries. A team's first April game otherwise
-- has no features at all, and last September is genuinely informative about this
-- April — weaker than in-season form, but better than null. `games_in_window` is
-- exposed so a consumer can down-weight or drop thin rows.
{{ config(materialized='table') }}

{% set window = 30 %}

with base as (
    select
        game_pk, game_date, season, season_phase, team_id,
        off_xwoba, def_xwoba, off_woba, def_woba,
        off_run_exp, def_run_exp, net_run_exp,
        off_hard_hit_rate, def_hard_hit_rate
    from {{ ref('sv_statcast_team_game') }}
),

rolled as (
    select
        game_pk,
        game_date,
        season,
        season_phase,
        team_id,
        {% for col in ['off_xwoba', 'def_xwoba', 'off_woba', 'def_woba',
                       'off_run_exp', 'def_run_exp', 'net_run_exp',
                       'off_hard_hit_rate', 'def_hard_hit_rate'] %}
        avg({{ col }}) over (
            partition by team_id order by game_date, game_pk
            rows between {{ window }} preceding and 1 preceding
        ) as form_{{ col }},
        {% endfor %}
        count(*) over (
            partition by team_id order by game_date, game_pk
            rows between {{ window }} preceding and 1 preceding
        ) as games_in_window
    from base
)

select * from rolled
