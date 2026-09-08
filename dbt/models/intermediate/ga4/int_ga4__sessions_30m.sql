{{ config(
    materialized='view',
    enabled=var('ga4_export_enabled', false),
    tags=['ga4_intermediate']
) }}

{% set taxonomy = var('ga4_channel_taxonomy', []) %}

-- One row per custom 30-minute session, enriched with deterministic
-- entry/landing attributes and session shape. Null-identity events remain
-- visible in int_ga4__session_event_map_30m and are intentionally excluded
-- here. The entry event is the first page/screen event by the same ordering
-- the sessionizer uses, so results are stable under replay.
with sessions as (
    select
        property_id,
        stream_id,
        user_pseudo_id,
        custom_session_key,
        min(native_session_key) as one_native_session_key,
        min(event_ts_utc) as session_start_ts_utc,
        max(event_ts_utc) as session_end_ts_utc,
        min(event_date_property) as session_date_property,
        count(*) as event_count,
        countif(event_name in ('page_view', 'screen_view')) as page_event_count,
        countif(event_name = 'purchase') as purchase_event_count,
        countif(transaction_id is not null) as transaction_event_count,
        array_agg(
            struct(
                page_location,
                page_referrer,
                collected_manual_source,
                collected_manual_medium,
                collected_manual_campaign,
                collected_gclid,
                collected_dclid,
                collected_srsltid
            ) order by
                event_ts_utc,
                source_event_json
            limit 1
        )[safe_offset(0)] as entry_event
    from {{ ref('int_ga4__session_event_map_30m') }}
    where custom_session_key is not null
    group by property_id, stream_id, user_pseudo_id, custom_session_key
)
select
    property_id,
    stream_id,
    user_pseudo_id,
    custom_session_key,
    one_native_session_key,
    session_start_ts_utc,
    session_end_ts_utc,
    timestamp_diff(session_end_ts_utc, session_start_ts_utc, second) as session_duration_seconds,
    session_date_property,
    event_count,
    page_event_count,
    purchase_event_count,
    transaction_event_count,
    page_event_count = 1 as is_bounced,
    entry_event.page_location as entry_page_location,
    entry_event.collected_manual_source as entry_source,
    entry_event.collected_manual_medium as entry_medium,
    entry_event.collected_manual_campaign as entry_campaign,
    entry_event.collected_gclid as entry_gclid,
    entry_event.page_referrer as entry_page_referrer,
    {{ ga4_channel_group('entry_event', taxonomy) }} as entry_channel_group
from sessions
