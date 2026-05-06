with annual_hcpcs as (
    select
        practice_state,
        hcpcs_code,
        extract(year from claim_month)::int as claim_year,
        sum(total_paid) as total_paid,
        sum(total_claims) as total_claims,
        sum(unique_beneficiaries) as total_beneficiaries
    from {{ ref('int_claims_with_state') }}
    where practice_state is not null
    group by 1, 2, 3
),

national_hcpcs as (
    select
        'NATIONAL' as practice_state,
        hcpcs_code,
        claim_year,
        sum(total_paid) as total_paid,
        sum(total_claims) as total_claims,
        sum(total_beneficiaries) as total_beneficiaries
    from annual_hcpcs
    group by 2, 3
),

combined as (
    select * from annual_hcpcs
    union all
    select * from national_hcpcs
),

ranked as (
    select
        *,
        row_number() over (
            partition by practice_state, claim_year 
            order by total_paid desc
        ) as rank_by_spend,
        row_number() over (
            partition by practice_state, claim_year 
            order by total_claims desc
        ) as rank_by_claims,
        row_number() over (
            partition by practice_state, claim_year 
            order by total_beneficiaries desc
        ) as rank_by_beneficiaries
    from combined
)

select *
from ranked
where rank_by_spend <= 50
   or rank_by_claims <= 50
   or rank_by_beneficiaries <= 50

