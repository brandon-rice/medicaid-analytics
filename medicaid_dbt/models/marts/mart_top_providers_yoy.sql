with annual_provider_spend as (
    select
        practice_state,
        billing_npi,
        provider_name,
        primary_taxonomy,
        entity_type_code,
        extract(year from claim_month)::int as claim_year,
        sum(total_paid) as total_paid,
        sum(total_claims) as total_claims,
        sum(unique_beneficiaries) as total_beneficiaries
    from {{ ref('int_claims_with_state') }}
    where practice_state is not null
    group by 1, 2, 3, 4, 5, 6
),

ranked as (
    select
        *,
        row_number() over (
            partition by practice_state, claim_year
            order by total_paid desc
        ) as state_rank,
        row_number() over (
            partition by claim_year
            order by total_paid desc
        ) as national_rank
    from annual_provider_spend
)

select *
from ranked
where state_rank <= 50 or national_rank <= 50