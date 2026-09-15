-- Batters and pitchers are credited from the same plate appearances with opposite
-- signs, so within any season and phase the league-wide batter RE24 and pitcher
-- RE24 must cancel exactly. A mismatch means a PA reached one role's aggregate
-- and not the other's -- a join dropped it -- and every published leaderboard
-- would be built on a different population from the one it claims.
select season, season_phase, sum(re24) filter (where role = 'batter') as batting,
       sum(re24) filter (where role = 'pitcher') as pitching
from {{ ref('gd_player_re24') }}
group by 1, 2
having abs(batting + pitching) > 1e-6
