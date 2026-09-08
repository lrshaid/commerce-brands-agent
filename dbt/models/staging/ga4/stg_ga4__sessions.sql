{{ config(
    materialized='view',
    enabled=var('ga4_export_enabled', false),
    tags=['ga4_staging']
) }}

-- GA4-native session grain only. This deliberately does not apply the
-- project's separate 30-minute tracker rule or stitch identities.
select
    property_id,
    stream_id,
    user_pseudo_id,
    ga_session_id,
    native_session_key,
    min(event_ts_utc) as session_start_ts_utc,
    max(event_ts_utc) as session_end_ts_utc,
    min(event_date_property) as session_date_property,
    count(*) as event_count,
    countif(event_name in ('page_view', 'screen_view')) as page_event_count,
    countif(event_name = 'purchase') as purchase_event_count,
    countif(missing_purchase_transaction_id) as purchase_events_missing_transaction_id
from {{ ref('stg_ga4__events') }}
where native_session_key is not null
group by
    property_id,
    stream_id,
    user_pseudo_id,
    ga_session_id,
    native_session_key
