-- Gold: the shape of a pitcher's good night versus his bad night.
--
-- Grain: (era, outcome).
--
-- **Split on fastball velocity, not on how the outing went.** This is the whole point
-- of the model and the reason it is built this way.
--
-- The obvious approach -- sort a pitcher's starts by runs or hits allowed, compare the
-- best third to the worst third -- is selection on the dependent variable. The "bad"
-- bucket then contains starts where he was fine and three flares fell in, so the
-- measured direction is partly a picture of bad luck rather than bad stuff.
--
-- Splitting on strikeout-minus-walk rate is no better in a subtler way: it sorts on
-- two of the nine outcomes being measured, so it mechanically exaggerates exactly
-- those two. Measured that way the walk rate appeared to more than double on a bad
-- night, which is an artefact of the split.
--
-- Average fastball velocity is observed *before* any outcome, is not one of the
-- quantities being measured, and is the standard indicator of whether a pitcher has
-- his stuff. Splitting a pitcher against his own season velocity therefore isolates
-- the direction without selecting on the answer.
--
-- **What it captures and what it misses.** Velocity tracks stuff, not command. A
-- pitcher can be off because he cannot locate while throwing his usual 95, and this
-- split will understate that. The direction here therefore leans toward the
-- strikeout axis rather than the walk axis. It is the honest trade for not selecting
-- on the outcome, and it is why only the *shape* is taken from this model while the
-- *size* of day-to-day form is estimated from a variance decomposition elsewhere.
{{ config(materialized='table') }}

with velo as (
    select
        pitcher,
        game_pk,
        -- br_statcast carries game_date, not season; the calendar year is the season
        -- for every game including a November World Series.
        any_value(year(game_date))                 as season,
        avg(release_speed)                         as velo
    from {{ ref('br_statcast') }}
    where pitch_type in ('FF', 'SI', 'FC') and release_speed is not null
    group by 1, 2
    having count(*) >= 20
),

qualified as (
    select
        v.*,
        case
            when v.season <= 2018 then '2015-2018'
            when v.season <= 2022 then '2019-2022'
            else '2023-2026'
        end as era,
        ntile(3) over (partition by v.pitcher, v.season order by v.velo) as tercile
    from velo v
    where (v.pitcher, v.season) in (
        select pitcher, season from velo group by 1, 2 having count(*) >= 15
    )
),

rates as (
    select
        q.era,
        q.tercile,
        p.outcome,
        count(*)::double / sum(count(*)) over (partition by q.era, q.tercile) as rate
    from {{ ref('sv_plate_appearances') }} p
    join qualified q on q.game_pk = p.game_pk and q.pitcher = p.pitcher
    where p.season_phase = 'regular'
    group by 1, 2, 3
)

select
    fast.era,
    fast.outcome,
    fast.rate                               as rate_when_on,
    slow.rate                               as rate_when_off,
    ln(slow.rate / nullif(fast.rate, 0))    as log_ratio
from rates fast
join rates slow on slow.era = fast.era and slow.outcome = fast.outcome and slow.tercile = 1
where fast.tercile = 3
