{{ config(
    materialized='view',
    enabled=var('ga4_export_enabled', false),
    tags=['ga4_intermediate', 'intermediate_view']
) }}

{% set identifier_fields = var('ga4_shopify_order_identifier_fields', []) %}

-- One output row per GA4 purchase observation. Matching is exact string
-- equality against explicitly configured Shopify identifiers; no prefix,
-- numeric coercion, or stripping is performed.
with purchases as (
    select
        observation_key as purchase_observation_key,
        event_ts_utc as purchase_ts_utc,
        event_date_property as purchase_date_property,
        user_pseudo_id,
        native_session_key,
        custom_session_key,
        transaction_id,
        source_event_json,
        analytics_storage_consent,
        ads_storage_consent,
        uses_transient_token
    from {{ ref('int_ga4__session_event_map_30m') }}
    where event_name = 'purchase'
), configured_order_identifiers as (
    {% if 'order_gid' in identifier_fields %}
    select order_gid, order_name, 'order_gid' as identifier_type, order_gid as identifier
    from {{ ref('stg_shopify__orders') }}
    where order_gid is not null
    {% else %}
    select cast(null as string) as order_gid, cast(null as string) as order_name,
           cast(null as string) as identifier_type, cast(null as string) as identifier
    where false
    {% endif %}
    {% if 'order_name' in identifier_fields %}
    union all
    select order_gid, order_name, 'order_name' as identifier_type, order_name as identifier
    from {{ ref('stg_shopify__orders') }}
    where order_name is not null
    {% endif %}
    {% if 'order_number' in identifier_fields %}
    union all
    select order_gid, order_name, 'order_number' as identifier_type,
           json_value(original_payload, '$.orderNumber') as identifier
    from {{ ref('stg_shopify__orders') }}
    where json_value(original_payload, '$.orderNumber') is not null
    {% endif %}
), matched_orders as (
    select distinct
        p.purchase_observation_key,
        p.purchase_ts_utc,
        p.source_event_json,
        i.order_gid,
        i.order_name,
        i.identifier_type
    from purchases p
    inner join configured_order_identifiers i
        on p.transaction_id = i.identifier
), match_summary as (
    select
        purchase_observation_key,
        purchase_ts_utc,
        source_event_json,
        count(distinct order_gid) as matched_order_count,
        array_agg(distinct order_gid order by order_gid limit 1)[safe_offset(0)] as matched_order_gid,
        array_agg(distinct order_name order by order_name limit 1)[safe_offset(0)] as matched_order_name,
        string_agg(distinct identifier_type, ',' order by identifier_type) as matched_identifier_types
    from matched_orders
    group by purchase_observation_key, purchase_ts_utc, source_event_json
), configuration as (
    select {{ identifier_fields | length }} as configured_identifier_count
)
select
    p.purchase_observation_key,
    p.purchase_ts_utc,
    p.purchase_date_property,
    p.user_pseudo_id,
    p.native_session_key,
    p.custom_session_key,
    p.transaction_id,
    p.source_event_json,
    p.analytics_storage_consent,
    p.ads_storage_consent,
    p.uses_transient_token,
    s.matched_order_gid,
    s.matched_order_name,
    s.matched_identifier_types,
    coalesce(s.matched_order_count, 0) as matched_order_count,
    case
        when p.transaction_id is null or p.transaction_id = '' then 'unmatched_missing_transaction_id'
        when c.configured_identifier_count = 0 then 'unmatched_no_identifier_mapping'
        when coalesce(s.matched_order_count, 0) = 0 then 'unmatched_no_exact_order'
        when s.matched_order_count > 1 then 'ambiguous_multiple_orders'
        else 'matched_exact_identifier'
    end as order_match_status
from purchases p
cross join configuration c
left join match_summary s
    on p.purchase_observation_key = s.purchase_observation_key
    and p.purchase_ts_utc = s.purchase_ts_utc
    and p.source_event_json = s.source_event_json
