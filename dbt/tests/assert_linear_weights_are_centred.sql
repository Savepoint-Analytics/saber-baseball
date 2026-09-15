-- Linear weights are the mean RE24 over the population the RE matrix was fitted
-- on, so their PA-weighted mean should be near zero: RE24 telescopes across a
-- complete half-inning to (runs scored - RE at the start), and RE is that
-- expectation. It is not *exactly* zero, for two measured reasons (2025):
--
--   * RE(bases empty, none out) pools leadoff PAs (0.502 runs to end) with PAs
--     after a leadoff home run (0.571) -- innings hang together, and the
--     matrix is Markov. Worth about -0.003 runs per half-inning.
--   * A bottom half from the ninth on is only "complete" if the home side
--     failed to score, so the ghost-runner starts in extras are selected low
--     (0.85 runs vs RE 1.14). About -110 runs a season.
--
-- Together they leave the mean at -0.0003 to -0.0014 runs per PA, under a run
-- per 600 PA and the same constant for every player. The threshold is set just
-- above that measured size: a drift past it means the two models disagree
-- about which half-innings are complete, which would offset every "runs above
-- average" by a constant.
select season, sum(run_value * plate_appearances) / sum(plate_appearances) as weighted_mean
from {{ ref('gd_linear_weights') }}
group by 1
having abs(weighted_mean) > 2e-3
