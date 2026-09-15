-- Where both Lahman and Statcast can say which side a player bats from, they must
-- agree at least 99% of the time. The derivation exists so that players Lahman
-- has not yet catalogued get a handedness; this checks that it is the same
-- handedness Lahman would have given them, on the population where we can tell.
-- (Not 100%: Lahman and Savant occasionally disagree on a switch hitter who has
-- all but abandoned one side.)
with both_known as (
    select bats, bats_statcast
    from {{ ref('sv_players') }}
    where bats_statcast is not null and in_lahman and statcast_pa >= 50
),
lahman_side as (
    -- `bats` coalesces Lahman first, so on this population it *is* Lahman's value.
    select
        count(*)                                        as n,
        count(*) filter (where bats <> bats_statcast)   as disagreements
    from both_known
)
select n, disagreements, disagreements / n as disagreement_rate
from lahman_side
where disagreements / n > 0.01
