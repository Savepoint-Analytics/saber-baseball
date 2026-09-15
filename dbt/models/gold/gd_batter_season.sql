-- Gold: one row per batter-season — the counting stats, rates and run values that
-- a player value card leads with. Every number traces to sv_plate_appearances
-- through gd_pa_run_value; nothing here is pulled from a published leaderboard.
--
-- Grain: (season, season_phase, player_id).
--
-- Three families of value, deliberately kept side by side:
--
--   * **re24** -- context-included. What his PAs did to expected runs in the
--     situations he faced. Rewards (and penalises) the timing of hits.
--   * **lw_raa** -- context-neutral linear-weights runs above average: each
--     outcome at its season-average run value (gd_linear_weights). The same events
--     with the situations averaged out. re24 - lw_raa is, roughly, "clutch", and
--     it does not persist year to year (r ~ +0.04), which is why both are shown.
--   * **woba / xwoba** -- Savant's rate stats, on the OBP scale. Carried because
--     they are what a baseball reader expects to see next to the run values.
--
-- **Park adjustment** uses the standard wRC+ correction: a league-average hitter
-- in a park with factor PF produces (PF - 1) * league runs-per-PA extra runs per
-- PA through no skill of his own, so that amount is removed from his runs above
-- average. The factor is PA-weighted over the venues he actually batted in, so a
-- Rockies hitter's road PAs are not adjusted as if they were at Coors. Venues
-- with no factor (the first two seasons, before a three-season window exists)
-- are treated as neutral. Both re24 and lw_raa get the adjustment; wOBA does not,
-- because it is not in runs and a runs factor would need a scale constant this
-- project has not measured.
--
-- `on_base_rate` counts reaching on error, matching the simulator's definition
-- (src.simulation.form.ON_BASE); it is not OBP, which excludes ROE and includes
-- sacrifice flies. Rates are raw, not shrunk: shrinkage toward league lives in the
-- odds-ratio model and is joined in by src.features.player_value, where the
-- regression constant is estimated rather than assumed.
{{ config(materialized='table') }}

with pa as (
    select *
    from {{ ref('gd_pa_run_value') }}
),

lw as (
    select season, outcome, run_value
    from {{ ref('gd_linear_weights') }}
),

-- League runs per PA, per season, regular season only -- the scale the park
-- correction is expressed in. Postseason rows use their season's regular-season
-- value: the run environment is a property of the season, not of October.
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
        batter                                            as player_id,
        count(distinct game_pk)                           as games,
        count(*)                                          as pa,
        count(*) filter (where outcome = 'K')             as k,
        count(*) filter (where outcome = 'BB')            as bb,
        count(*) filter (where outcome = 'HBP')           as hbp,
        count(*) filter (where outcome = '1B')            as singles,
        count(*) filter (where outcome = '2B')            as doubles,
        count(*) filter (where outcome = '3B')            as triples,
        count(*) filter (where outcome = 'HR')            as hr,
        count(*) filter (where outcome = 'ROE')           as roe,
        count(*) filter (where outcome = 'OUT')           as outs,
        sum(runs_on_play)                                 as runs_on_play,
        sum(woba_value)                                   as woba_num,
        sum(woba_denom)                                   as woba_denom,
        avg(xwoba) filter (where woba_denom > 0)          as xwoba,
        sum(re24)                                         as re24,
        sum(lw_value)                                     as lw_raa,
        avg(pa_park_factor)                               as park_factor
    from per_pa
    group by 1, 2, 3
)

select
    a.season,
    a.season_phase,
    a.player_id,
    a.games,
    a.pa,
    a.k,
    a.bb,
    a.hbp,
    a.singles,
    a.doubles,
    a.triples,
    a.hr,
    a.roe,
    a.outs,
    a.k / a.pa                                            as k_pct,
    a.bb / a.pa                                           as bb_pct,
    (a.pa - a.k - a.outs) / a.pa                          as on_base_rate,
    a.woba_num / nullif(a.woba_denom, 0)                  as woba,
    a.xwoba,
    a.runs_on_play,
    a.re24,
    a.lw_raa,
    a.park_factor,
    l.lg_runs_per_pa,
    a.re24   - (a.park_factor - 1.0) * l.lg_runs_per_pa * a.pa as re24_park_adj,
    a.lw_raa - (a.park_factor - 1.0) * l.lg_runs_per_pa * a.pa as lw_raa_park_adj
from agg a
join league l using (season)
