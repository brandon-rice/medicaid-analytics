select
    billing_provider_npi   as billing_npi,
    servicing_provider_npi as servicing_npi,
    hcpcs_code,
    to_date(claim_from_month || '-01', 'YYYY-MM-DD') as claim_month,
    unique_beneficiaries,
    total_claims,
    total_paid
from {{ source('medicaid_raw', 'medicaid_claims_raw') }}