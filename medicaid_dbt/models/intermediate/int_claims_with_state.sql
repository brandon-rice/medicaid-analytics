select
    c.billing_npi,
    c.servicing_npi,
    c.hcpcs_code,
    c.claim_month,
    c.unique_beneficiaries,
    c.total_claims,
    c.total_paid,
    p.practice_state,
    p.provider_name,
    p.primary_taxonomy,
    p.entity_type_code
from {{ ref('stg_claims') }} c
left join {{ ref('stg_providers') }} p
    on c.billing_npi = p.npi