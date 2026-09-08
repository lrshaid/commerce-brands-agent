{{ config(
    materialized='view',
    enabled=var('ga4_export_enabled', false),
    tags=['ga4_staging']
) }}

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
    ecommerce_purchase_revenue,
    ecommerce_purchase_revenue_in_usd,
    page_location,
    page_referrer,
    page_title,
    analytics_storage_consent,
    ads_storage_consent,
    uses_transient_token,
    collected_manual_source,
    collected_manual_medium,
    collected_manual_campaign,
    collected_gclid,
    collected_dclid,
    collected_srsltid,
    collected_traffic_source_json,
    session_traffic_source_json,
    source_event_json,
    missing_property_id,
    missing_user_pseudo_id,
    missing_ga_session_id
from {{ ref('stg_ga4__events') }}
where event_name in ('page_view', 'screen_view')
