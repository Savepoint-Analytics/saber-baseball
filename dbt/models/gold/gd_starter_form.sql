-- Gold: rolling form for each *starting* pitcher, as known before each of their starts.
--
-- Grain: (game_pk, pitcher) — one row per start, carrying that starter's form going in.
--
-- CAUSALITY: every window is `rows between N preceding and 1 preceding`, excluding the
-- current start. Same rule and same reason as gd_team_form — including the current row
-- would fold a start's own result into the features used to predict it.
--
-- The window is 10 *starts*, not 30 games: a starter appears every fifth day, so 10
-- starts is roughly two months of his own work. Rolling over calendar games instead
-- would mix in the four days he did not pitch and blur the very thing being measured.
--
-- The TTO splits are the point of this model. A starter who is excellent the first
-- time through and collapses the third is a materially different asset from one who
-- holds steady, and team-level aggregates cannot express that distinction at all —
-- which is why gd_team_form failed to beat Elo (see docs/methodology.md).
--
-- Relief appearances are excluded from the window: a starter's occasional bullpen
-- outing is a different workload and would contaminate the durability signal.
{{ config(materialized='table') }}

{% set window = 10 %}

with starts as (
    select
        game_pk, game_date, season, season_phase, pitcher, team_id,
        batters_faced, xwoba_allowed,
        xwoba_allowed_tto1, xwoba_allowed_tto2, xwoba_allowed_tto3plus,
        run_exp_allowed, strikeouts, walks
    from {{ ref('sv_statcast_pitcher_game') }}
    where is_starter
),

rolled as (
    select
        game_pk,
        game_date,
        season,
        season_phase,
        pitcher,
        team_id,
        {% for col in ['xwoba_allowed', 'xwoba_allowed_tto1', 'xwoba_allowed_tto2',
                       'xwoba_allowed_tto3plus', 'batters_faced', 'run_exp_allowed',
                       'strikeouts', 'walks'] %}
        avg({{ col }}) over (
            partition by pitcher order by game_date, game_pk
            rows between {{ window }} preceding and 1 preceding
        ) as sp_form_{{ col }},
        {% endfor %}
        count(*) over (
            partition by pitcher order by game_date, game_pk
            rows between {{ window }} preceding and 1 preceding
        ) as starts_in_window
    from starts
)

select
    *,
    -- How much worse a starter gets deep into a game. Positive = fades. Exposed as
    -- its own column because it is the feature the whole model exists to test, and a
    -- linear model cannot construct a difference of two of its own inputs.
    sp_form_xwoba_allowed_tto3plus - sp_form_xwoba_allowed_tto1 as sp_form_tto_penalty
from rolled
