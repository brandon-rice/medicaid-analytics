select
    npi,
    entity_type_code,
    case
        when entity_type_code = '2' then organization_name
        else trim(coalesce(first_name, '') || ' ' || coalesce(last_name, ''))
    end as provider_name,
    practice_state,
    practice_city,
    practice_zip,
    primary_taxonomy
from {{ source('medicaid_raw', 'nppes_raw') }}
where practice_state is not null