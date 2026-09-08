{{ config(
    materialized='view',
    enabled=var('ga4_export_enabled', false),
    tags=['ga4_staging']
) }}

-- Purchases retain the native ecommerce transaction_id when present. A
-- missing ID is surfaced as a flag; rows are not assigned an invented key.
select
    property_id,
    export_table_suffix,
    event_date_property,
    event_date_parsed,
    event_ts_utc,
    event_timestamp_micros,
    event_name,
    user_pseudo_id,
    stream_id,
    ga_session_id,
    native_session_key,
    transaction_id,
    ecommerce_purchase_revenue,
    ecommerce_purchase_revenue_in_usd,
    analytics_storage_consent,
    ads_storage_consent,
    uses_transient_token,
    session_traffic_source_json,
    source_event_json,
    missing_property_id,
    missing_user_pseudo_id,
    missing_ga_session_id,
    missing_purchase_transaction_id
from {{ ref('stg_ga4__events') }}
where event_name = 'purchase'
