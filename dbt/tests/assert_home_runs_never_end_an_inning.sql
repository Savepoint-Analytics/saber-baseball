-- A home run cannot record an out, so it can never transition to the absorbing
-- three-out state. This test exists because that invariant was violated: the
-- walk-off filter in gd_base_transitions silently matched nothing, letting truncated
-- half-innings put 1.25% of bases-loaded home runs into state 24. Nothing else in
-- the suite caught it -- probabilities still summed to 1 and every range test passed.
select *
from {{ ref('gd_base_transitions') }}
where outcome = 'HR' and state_after = 24
