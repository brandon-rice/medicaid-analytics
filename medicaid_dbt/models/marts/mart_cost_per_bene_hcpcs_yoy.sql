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

with_cpb as (
    select
        practice_state,
        hcpcs_code,
        claim_year,
        total_paid,
        total_claims,
        total_beneficiaries,
        case
            when total_beneficiaries > 0 
                then total_paid / total_beneficiaries
            else null
        end as cost_per_beneficiary
    from combined
    where total_beneficiaries >= 100
),

with_yoy as (
    select
        *,
        lag(cost_per_beneficiary) over (
            partition by practice_state, hcpcs_code 
            order by claim_year
        ) as prior_year_cpb,
        lag(claim_year) over (
            partition by practice_state, hcpcs_code 
            order by claim_year
        ) as prior_year,
        first_value(cost_per_beneficiary) over (
            partition by practice_state, hcpcs_code 
            order by claim_year
            rows between unbounded preceding and unbounded following
        ) as first_year_cpb,
        first_value(claim_year) over (
            partition by practice_state, hcpcs_code 
            order by claim_year
            rows between unbounded preceding and unbounded following
        ) as first_year,
        count(*) over (
            partition by practice_state, hcpcs_code
        ) as years_observed
    from with_cpb
),

with_regression as (
    select
        practice_state,
        hcpcs_code,
        regr_slope(cost_per_beneficiary, claim_year) as cpb_slope,
        regr_intercept(cost_per_beneficiary, claim_year) as cpb_intercept,
        regr_r2(cost_per_beneficiary, claim_year) as cpb_r2
    from with_cpb
    group by 1, 2
)

select
    y.practice_state,
    y.hcpcs_code,
    y.claim_year,
    y.total_paid,
    y.total_claims,
    y.total_beneficiaries,
    y.cost_per_beneficiary,
    y.prior_year_cpb,

    -- 1-year YOY %
    case
        when y.prior_year_cpb is null or y.prior_year_cpb <= 0 then null
        when y.cost_per_beneficiary is null then null
        when y.prior_year != y.claim_year - 1 then null
        else (y.cost_per_beneficiary - y.prior_year_cpb) / y.prior_year_cpb
    end as yoy_pct_change,

    -- Cumulative % change from first observed year to current year
    case
        when y.first_year_cpb is null or y.first_year_cpb <= 0 then null
        when y.cost_per_beneficiary is null then null
        when y.first_year = y.claim_year then null
        else (y.cost_per_beneficiary - y.first_year_cpb) / y.first_year_cpb
    end as cumulative_pct_change,

    -- CAGR from first observed year to current year
    case
        when y.first_year_cpb is null or y.first_year_cpb <= 0 then null
        when y.cost_per_beneficiary is null or y.cost_per_beneficiary <= 0 then null
        when y.first_year = y.claim_year then null
        else power(y.cost_per_beneficiary / y.first_year_cpb, 1.0 / (y.claim_year - y.first_year)) - 1
    end as cagr_to_date,

    y.first_year,
    y.first_year_cpb,
    y.years_observed,

    -- Regression-based trend metrics (same value for every year of a code)
    r.cpb_slope as trend_slope_per_year,
    r.cpb_r2 as trend_fit_quality

from with_yoy y
left join with_regression r
    on y.practice_state = r.practice_state
    and y.hcpcs_code = r.hcpcs_code