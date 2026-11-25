-- Gold: one row per pitcher-season — the mirror of gd_batter_season from the
-- mound. Same run values, same park correction, opposite sign: positive is runs
-- prevented relative to league expectation.
--
-- Grain: (season, season_phase, player_id).
--
-- Counting stats are batters faced and outcomes allowed; rates are per batter
-- faced. `re24_saved` is the negative of the RE24 the batters he faced accrued,
-- `lw_raa_saved` the context-neutral version at season linear weights. The park
-- correction runs the other way from the batter's: a pitcher in a hitter's park
-- allows (PF - 1) * league runs-per-PA extra runs per batter through no fault of
-- his own, so that is *added back* to his runs saved.
--
-- Starts come from sv_statcast_pitcher_game, where the starter is whoever threw
-- his side's first plate appearance of the game. Games is every appearance.
-- wpa / wpa_per_li / mean_leverage are in wins, sign-flipped like the runs;
-- mean_leverage is what separates a closer from a mop-up man at equal quality.
{{ config(materialized='table') }}

with pa as (
    select *
    from {{ ref('gd_pa_run_value') }}
),

lw as (
    select season, outcome, run_value
    from {{ ref('gd_linear_weights') }}
),

league as (
    select season, sum(runs_on_play) / count(*) as lg_runs_per_pa
    from pa
    where season_phase = 'regular'
    group by 1
),

parks as (
    select season, venue_id, park_factor
    from {{ ref('gd_park_factor') }}
),

starts as (
    select season, season_phase, pitcher as player_id, count(*) as starts
    from {{ ref('sv_statcast_pitcher_game') }}
    where is_starter
    group by 1, 2, 3
),

per_pa as (
    select
        p.*,
        w.run_value                            as lw_value,
        coalesce(k.park_factor, 1.0)           as pa_park_factor
    from pa p
    join lw w on w.season = p.season and w.outcome = p.outcome
    left join parks k on k.season = p.season and k.venue_id = p.venue_id
),

agg as (
    select
        season,
        season_phase,
        pitcher                                           as player_id,
        count(distinct game_pk)                           as games,
        count(*)                                          as bf,
        count(*) filter (where outcome = 'K')             as k,
        count(*) filter (where outcome = 'BB')            as bb,
        count(*) filter (where outcome = 'HBP')           as hbp,
        count(*) filter (where outcome = '1B')            as singles,
        count(*) filter (where outcome = '2B')            as doubles,
        count(*) filter (where outcome = '3B')            as triples,
        count(*) filter (where outcome = 'HR')            as hr,
        count(*) filter (where outcome = 'ROE')           as roe,
        count(*) filter (where outcome = 'OUT')           as outs,
        sum(runs_on_play)                                 as runs_allowed,
        sum(woba_value)                                   as woba_num,
        sum(woba_denom)                                   as woba_denom,
        avg(xwoba) filter (where woba_denom > 0)          as xwoba_allowed,
        -sum(re24)                                        as re24_saved,
        -sum(lw_value)                                    as lw_raa_saved,
        -sum(wpa)                                         as wpa,
        -sum(wpa / nullif(leverage, 0))                   as wpa_per_li,
        avg(leverage)                                     as mean_leverage,
        avg(pa_park_factor)                               as park_factor
    from per_pa
    group by 1, 2, 3
)

select
    a.season,
    a.season_phase,
    a.player_id,
    a.games,
    coalesce(s.starts, 0)                                 as starts,
    a.bf,
    a.k,
    a.bb,
    a.hbp,
    a.singles,
    a.doubles,
    a.triples,
    a.hr,
    a.roe,
    a.outs,
    a.k / a.bf                                            as k_pct,
    a.bb / a.bf                                           as bb_pct,
    (a.bf - a.k - a.outs) / a.bf                          as on_base_allowed,
    a.woba_num / nullif(a.woba_denom, 0)                  as woba_allowed,
    a.xwoba_allowed,
    a.runs_allowed,
    a.re24_saved,
    a.lw_raa_saved,
    a.wpa,
    a.wpa_per_li,
    a.mean_leverage,
    a.park_factor,
    l.lg_runs_per_pa,
    a.re24_saved   + (a.park_factor - 1.0) * l.lg_runs_per_pa * a.bf as re24_saved_park_adj,
    a.lw_raa_saved + (a.park_factor - 1.0) * l.lg_runs_per_pa * a.bf as lw_raa_saved_park_adj
from agg a
join league l using (season)
left join starts s using (season, season_phase, player_id)
