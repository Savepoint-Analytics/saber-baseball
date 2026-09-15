-- Each (era, state, outcome) is a probability distribution over outcomes-and-runs.
-- If it does not sum to 1 the simulator will silently under- or over-sample from it.
select era, state_before, outcome, sum(prob) as total
from {{ ref('gd_base_transitions') }}
group by 1, 2, 3
having abs(sum(prob) - 1.0) > 1e-9
