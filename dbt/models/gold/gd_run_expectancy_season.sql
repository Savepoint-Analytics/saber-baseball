-- Gold: the RE24 matrix per SEASON — the baseline player value is measured against.
--
-- Grain: (season, state) — 24 states per season.
--
-- gd_run_expectancy is bucketed by era because the simulator wants a stable,
-- well-populated matrix. Player evaluation wants the opposite trade: "runs above
-- average" has to mean above *this season's* average, or a hitter in a low-scoring
-- year is docked for the run environment. Measured against the era matrix, the
-- whole 2015 league summed to -1,407 RE24 (2015 scored 4.25 runs a game inside an
-- era that averaged more), which is not a statement about any player. Per season
-- the league sums to roughly zero by construction, which is what "above average"
-- is supposed to mean.
--
-- Sample size is the trade, and it is affordable: ~185,000 regular-season PAs a
-- season leave ~400 in the rarest state (runner on third, none out), a standard
-- error around 0.06 runs there and far less everywhere else. The 60-game 2020
-- season is the exception (143 PAs in that state) and its player values carry
-- correspondingly more noise. Same population rules as the era matrix — regular
-- season, complete half-innings only.
{{ config(materialized='table') }}

select
    season,
    state_before                         as state,
    outs_before                          as outs,
    b1_before                            as on_1b,
    b2_before                            as on_2b,
    b3_before                            as on_3b,
    count(*)                             as plate_appearances,
    avg(runs_to_end_of_inning)           as run_expectancy
from {{ ref('sv_plate_appearances') }}
where season_phase = 'regular'
  and in_complete_half_inning
group by 1, 2, 3, 4, 5, 6
