select
    practice_state,
    claim_month,
    sum(total_paid) as total_paid,
    sum(total_claims) as total_claims,
    sum(unique_beneficiaries) as total_beneficiaries
from {{ ref('int_claims_with_state') }}
where practice_state is not null
group by practice_state, claim_month