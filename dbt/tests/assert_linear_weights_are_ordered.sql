-- The classic sanity check on any run-expectancy-derived linear weights: a home
-- run is worth more than a triple, a triple more than a double, a double more
-- than a single, a single more than a walk, and an out costs runs. Every season
-- must satisfy it. If any pair inverts, the RE matrix or the after-state
-- encoding is wrong; baseball is not that surprising.
with w as (
    select season, outcome, run_value from {{ ref('gd_linear_weights') }}
),
pairs as (
    select 'HR' as better, '3B' as worse union all
    select '3B', '2B' union all
    select '2B', '1B' union all
    select '1B', 'BB' union all
    select 'BB', 'OUT' union all
    select 'BB', 'K'
)
select a.season, p.better, a.run_value as better_value, p.worse, b.run_value as worse_value
from pairs p
join w a on a.outcome = p.better
join w b on b.outcome = p.worse and b.season = a.season
where a.run_value <= b.run_value
union all
select season, outcome, run_value, 'zero', 0.0
from w
where outcome in ('K', 'OUT') and run_value >= 0
