-- Holding the bases fixed, expected runs must fall as outs rise. This is the
-- sanity property of a run-expectancy matrix; a violation means the state encoding
-- or the inning-completion logic is wrong, not that baseball is surprising.
-- Applied to both the era matrix and the per-season one.
with re as (
    select era as bucket, on_1b, on_2b, on_3b, outs, run_expectancy from {{ ref('gd_run_expectancy') }}
    union all
    select cast(season as varchar), on_1b, on_2b, on_3b, outs, run_expectancy from {{ ref('gd_run_expectancy_season') }}
)
select a.bucket, a.on_1b, a.on_2b, a.on_3b, a.outs as outs_a, b.outs as outs_b,
       a.run_expectancy as re_a, b.run_expectancy as re_b
from re a
join re b
  on a.bucket = b.bucket and a.on_1b = b.on_1b and a.on_2b = b.on_2b and a.on_3b = b.on_3b
 and b.outs = a.outs + 1
where b.run_expectancy >= a.run_expectancy
