{{ config(
    materialized='view',
    enabled=var('ga4_export_enabled', false),
    tags=['ga4_intermediate']
) }}

-- Custom tracker-compatible sessionization, kept separate from GA4's native
-- ga_session_id. Browser scope is property + stream + user_pseudo_id only;
-- there is intentionally no cross-device identity fallback here.
with ordered_events as (
    select
        e.*,
        lag(event_ts_utc) over (
            partition by property_id, stream_id, user_pseudo_id
            order by
                event_ts_utc,
                coalesce(event_bundle_sequence_id, -1),
                coalesce(batch_event_index, -1),
                coalesce(batch_ordering_id, -1),
                coalesce(batch_page_id, -1),
                source_event_json
        ) as previous_event_ts_utc
    from {{ ref('stg_ga4__events') }} as e
    where nullif(property_id, '') is not null
      and nullif(stream_id, '') is not null
      and nullif(user_pseudo_id, '') is not null
), marked_events as (
    select
        o.*,
        timestamp_diff(event_ts_utc, previous_event_ts_utc, second) as gap_seconds,
        case
            when previous_event_ts_utc is null then 1
            when timestamp_diff(event_ts_utc, previous_event_ts_utc, second) > 1800 then 1
            else 0
        end as is_custom_session_start
    from ordered_events as o
), numbered_events as (
    select
        m.*,
        sum(is_custom_session_start) over (
            partition by property_id, stream_id, user_pseudo_id
            order by
                event_ts_utc,
                coalesce(event_bundle_sequence_id, -1),
                coalesce(batch_event_index, -1),
                coalesce(batch_ordering_id, -1),
                coalesce(batch_page_id, -1),
                source_event_json
            rows between unbounded preceding and current row
        ) as custom_session_ordinal
    from marked_events as m
), valid_events as (
    select
        observation_key,
        property_id,
        stream_id,
        user_pseudo_id,
        native_session_key,
        concat(
            property_id, ':', stream_id, ':', user_pseudo_id,
            ':custom30m:', cast(custom_session_ordinal as string)
        ) as custom_session_key,
        ga_session_id,
        event_ts_utc,
        event_date_property,
        event_name,
        transaction_id,
        page_location,
        page_referrer,
        page_title,
        collected_manual_source,
        collected_manual_medium,
        collected_manual_campaign,
        collected_gclid,
        collected_dclid,
        collected_srsltid,
        analytics_storage_consent,
        ads_storage_consent,
        uses_transient_token,
        gap_seconds,
        is_custom_session_start,
        cast(null as string) as session_exclusion_reason,
        source_event_json
    from numbered_events
), excluded_events as (
    select
        observation_key,
        property_id,
        stream_id,
        user_pseudo_id,
        native_session_key,
        cast(null as string) as custom_session_key,
        ga_session_id,
        event_ts_utc,
        event_date_property,
        event_name,
        transaction_id,
        page_location,
        page_referrer,
        page_title,
        collected_manual_source,
        collected_manual_medium,
        collected_manual_campaign,
        collected_gclid,
        collected_dclid,
        collected_srsltid,
        analytics_storage_consent,
        ads_storage_consent,
        uses_transient_token,
        cast(null as int64) as gap_seconds,
        0 as is_custom_session_start,
        case
            when nullif(property_id, '') is null then 'missing_property_id'
            when nullif(stream_id, '') is null then 'missing_stream_id'
            when nullif(user_pseudo_id, '') is null then 'missing_user_pseudo_id'
        end as session_exclusion_reason,
        source_event_json
    from {{ ref('stg_ga4__events') }}
    where nullif(property_id, '') is null
       or nullif(stream_id, '') is null
       or nullif(user_pseudo_id, '') is null
)
select * from valid_events
union all
select * from excluded_events
