-- Gold: RE24 and WPA credited to each player per season, as a batter and as a
-- pitcher. The context-included player value numbers: what a player's plate
-- appearances did to expected runs, and to his team's chance of winning, in the
-- situations he actually faced. Phase 2's "how games are won" at player level.
--
-- Grain: (season, season_phase, role, player_id). A two-way player (Ohtani) has
-- one row per role.
--
-- **Sign convention: positive is good for the player in both roles.** A batter's
-- re24 is the sum of gd_pa_run_value.re24 over his PAs; a pitcher's is the
-- negative of the sum over the PAs he threw -- runs he prevented relative to
-- expectation. Summed over the whole league in a season the two roles therefore
-- cancel exactly, which `assert_player_re24_balances` checks.
--
-- RE24 is relative to the season's league-average expectation, so a full-time
-- average player sits near zero and the league total is near zero (not exactly:
-- postseason PAs and walk-offs are valued but were not in the matrix's fit).
-- It is *not* park- or opponent-adjusted; gd_batter_season and
-- gd_pitcher_season carry the park-adjusted versions.
--
-- **wpa** is in wins (a season of +5 is a star). **wpa_per_li** divides each
-- PA's WPA by its leverage before summing -- Tango's "context-neutral wins",
-- what the same events were worth at average leverage -- so wpa - wpa_per_li
-- is the part of a player's WPA that came from *when* he produced.
-- **mean_leverage** says how high-leverage his plate appearances were on
-- average (1.0 is league); a closer's runs to ~1.8, a mop-up man's well below 1.
{{ config(materialized='table') }}

with pa as (
    select season, season_phase, batter, pitcher, runs_on_play, re24, wpa, leverage
    from {{ ref('gd_pa_run_value') }}
),

batting as (
    select
        season,
        season_phase,
        'batter'                     as role,
        batter                       as player_id,
        count(*)                     as pa,
        sum(runs_on_play)            as runs_on_play,
        sum(re24)                    as re24,
        sum(wpa)                     as wpa,
        sum(wpa / nullif(leverage, 0)) as wpa_per_li,
        avg(leverage)                as mean_leverage
    from pa
    group by 1, 2, 3, 4
),

pitching as (
    select
        season,
        season_phase,
        'pitcher'                    as role,
        pitcher                      as player_id,
        count(*)                     as pa,
        sum(runs_on_play)            as runs_on_play,
        -sum(re24)                   as re24,
        -sum(wpa)                    as wpa,
        -sum(wpa / nullif(leverage, 0)) as wpa_per_li,
        avg(leverage)                as mean_leverage
    from pa
    group by 1, 2, 3, 4
)

select *, re24 / pa as re24_per_pa from batting
union all
select *, re24 / pa as re24_per_pa from pitching
