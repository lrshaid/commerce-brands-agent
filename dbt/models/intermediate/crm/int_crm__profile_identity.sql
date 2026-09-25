{{ config(materialized='view', tags=['intermediate_view', 'crm']) }}
-- Conservative identity: a profile observed with multiple email hashes is
-- ambiguous. Do not rewrite its history to whichever email appeared last.
with profiles as (
    select shop_key, profile_id,
        count(distinct email_identity_id) as email_identity_count,
        min(email_identity_id) as only_email_identity_id
    from {{ ref('int_crm__events') }}
    where profile_id is not null
    group by shop_key, profile_id
)
select shop_key, profile_id, email_identity_count,
    case when email_identity_count = 1 then only_email_identity_id end as customer_identity_id,
    case when email_identity_count = 1 then 'email_exact'
         when email_identity_count = 0 then 'no_email'
         else 'ambiguous_email' end as identity_status
from profiles
