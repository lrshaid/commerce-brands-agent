{{ config(
    materialized='view',
    enabled=var('ga4_export_enabled', false),
    tags=['ga4_intermediate', 'intermediate_view']
) }}

-- Links custom 30-minute sessions to a resolved customer identity when the
-- session contains events carrying a GA4 user_id equal to the project's hashed
-- email identity key (sha256(lower(trim(email)))). No identity is inferred when
-- user_id is absent; order-linked matching in fct_ga4__customer_attribution
-- remains the fallback. user_pseudo_id is never used as an identity key.
-- When a session mixes several user_ids, the lexicographically largest is
-- taken (documented simplification).
with sessions as (
    select
        custom_session_key,
        user_pseudo_id,
        session_date_property
    from {{ ref('int_ga4__sessions_30m') }}
),
session_user_ids as (
    select
        m.custom_session_key,
        max(e.user_id) as observed_user_id
    from {{ ref('int_ga4__session_event_map_30m') }} m
    join {{ ref('stg_ga4__events') }} e
        on e.observation_key = m.observation_key
    group by m.custom_session_key
)
select
    s.custom_session_key,
    s.user_pseudo_id,
    s.session_date_property,
    u.observed_user_id as ga4_user_id,
    i.customer_identity_id,
    i.canonical_customer_gid,
    i.is_email_based,
    i.customer_identity_id is null as missing_identity,
    case
        when u.observed_user_id is null then 'no_user_id'
        when i.customer_identity_id is null then 'user_id_unmatched'
        else 'matched_email_identity'
    end as identity_link_status
from sessions s
left join session_user_ids u
    on u.custom_session_key = s.custom_session_key
left join {{ ref('int_shopify__customer_identity') }} i
    on i.customer_identity_id = u.observed_user_id