-- Silver: cleaned, conformed game-level table from the StatsAPI schedule.
-- One row per completed game with typed scores and a derived home-win flag —
-- the spine for Phase-3 outcome modeling.
--
-- Two quirks of the StatsAPI schedule feed are handled here (both verified against
-- the 2025 pull: 2,511 raw rows → 2,481 games):
--
--   1. 'Completed Early' games are real, official results (rain-shortened) with
--      scores populated — 5 of them in 2025. Only 'Postponed' rows are score-less
--      and must be dropped, so filter on the completed statuses rather than on
--      'Final' alone.
--   2. A game_pk can appear under two dates: once on the original date (postponed
--      or suspended) and again on the date it actually finished. 34 game_pks in
--      2025, of which 4 are Final-on-both-dates (suspended games resumed the next
--      day), which the `unique` test on game_pk catches. We keep the row from the
--      date the game reached its final score. That de-duplicates the spine and
--      keeps the backtest causal — a result is never dated earlier than the day it
--      became known, so time-ordered features (Elo, rest days) can't peek ahead.
{{ config(materialized='table') }}

with src as (
    select *
    from {{ ref('br_statsapi_schedule') }}
    where status in ('Final', 'Completed Early')
),

-- Rank each game_pk's rows so we can keep only the completion-date row (see #2).
ranked as (
    select
        src.*,
        row_number() over (
            partition by game_pk
            order by cast(game_date as date) desc
        ) as _completion_rank
    from src
)

select
    cast(game_pk as bigint)                      as game_pk,
    cast(game_date as date)                      as game_date,
    cast(season as integer)                      as season,
    game_type,
    -- Postseason must never be pooled with regular season: it is a biased sample
    -- (only good teams appear, and only in short series), so it would distort any
    -- team-strength or Pythagorean estimate fit on it. `game_type` keeps the round
    -- detail — F=wild card, D=division, L=championship, W=World Series — while
    -- `season_phase` is the coarse split most consumers actually filter on.
    -- Note game-163 tiebreakers are typed 'R' by the API and so count as regular
    -- season, which is correct (2018 has 2,431 regular-season games for this reason).
    case when game_type = 'R' then 'regular' else 'postseason' end as season_phase,
    status,
    home_team_id,
    home_team,
    away_team_id,
    away_team,
    cast(home_score as integer)                  as home_score,
    cast(away_score as integer)                  as away_score,
    case
        when home_score is null or away_score is null then null
        when home_score > away_score then 1 else 0
    end                                          as home_win,
    venue,
    cast(venue_id as integer)                    as venue_id,
    day_night,
    weather_condition,
    -- Arrives as a string; a dome reports a constant indoor temperature rather than
    -- a null, so `is_indoor` below is what tells a model to ignore it.
    try_cast(weather_temp as integer)            as weather_temp_f,
    weather_wind,
    -- "7 mph, In From CF" -> 7. Retractable roofs report "0 mph, None".
    try_cast(regexp_extract(weather_wind, '^(\d+)', 1) as integer) as wind_mph,
    -- Direction relative to the batter. Out-blowing wind carries fly balls; in-blowing
    -- suppresses them; crosswinds mostly do neither. Collapsed to one signed feature
    -- because the raw string has ~20 variants that all reduce to this.
    case
        when weather_wind is null then null
        when weather_wind ilike '%out to%' then 1
        when weather_wind ilike '%in from%' then -1
        else 0
    end                                          as wind_out_sign,
    -- A closed roof or dome makes every other weather column meaningless. Flagging it
    -- lets a model learn "ignore temperature here" instead of treating a constant 70F
    -- dome reading as a real warm day.
    weather_condition in ('Roof Closed', 'Dome') as is_indoor,
    home_probable_pitcher_id,
    away_probable_pitcher_id
from ranked
where _completion_rank = 1
