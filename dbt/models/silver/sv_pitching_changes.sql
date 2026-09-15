-- Silver: every relief appearance with the score margin at the moment it began.
--
-- Grain: (game_pk, pitcher, first_at_bat).
--
-- Managers do not pick relievers at random, and they do not pick them purely by who
-- is rested. They pick by the score: the best arm goes into a one-run game and the
-- mop-up man goes into a 9-2 game. Measured over 2025 that gap is .3094 on-base
-- allowed in close games against .3269 in blowouts -- larger than a pitcher's own
-- day-to-day variation.
--
-- That makes it a *feedback* effect rather than a fixed property of the matchup: a
-- lead brings in worse pitching, which extends the lead. A simulator that draws
-- relievers by usage alone compresses both tails, producing too few blowouts and too
-- few tight games.
{{ config(materialized='table') }}

with pa as (
    select
        p.game_pk,
        p.season,
        p.season_phase,
        p.at_bat_number,
        p.pitcher,
        p.fld_team_id,
        p.inning_topbot,
        p.runs_on_play,
        s.is_starter
    from {{ ref('sv_plate_appearances') }} p
    join {{ ref('sv_statcast_pitcher_game') }} s
      on s.game_pk = p.game_pk and s.pitcher = p.pitcher
),

-- Running score *before* each plate appearance.
scored as (
    select
        *,
        coalesce(sum(case when inning_topbot = 'Bot' then runs_on_play else 0 end)
                 over (partition by game_pk order by at_bat_number
                       rows between unbounded preceding and 1 preceding), 0) as home_runs_before,
        coalesce(sum(case when inning_topbot = 'Top' then runs_on_play else 0 end)
                 over (partition by game_pk order by at_bat_number
                       rows between unbounded preceding and 1 preceding), 0) as away_runs_before
    from pa
),

appearances as (
    select
        game_pk,
        season,
        season_phase,
        pitcher,
        fld_team_id                                     as team_id,
        min(at_bat_number)                              as first_at_bat
    from scored
    where not is_starter
    group by 1, 2, 3, 4, 5
)

select
    a.game_pk,
    a.season,
    a.season_phase,
    a.pitcher,
    a.team_id,
    a.first_at_bat,
    abs(s.home_runs_before - s.away_runs_before)        as margin_on_entry,
    case
        when abs(s.home_runs_before - s.away_runs_before) <= 2 then 'close'
        when abs(s.home_runs_before - s.away_runs_before) <= 5 then 'medium'
        else 'blowout'
    end                                                 as leverage_band
from appearances a
join scored s
  on s.game_pk = a.game_pk and s.at_bat_number = a.first_at_bat
