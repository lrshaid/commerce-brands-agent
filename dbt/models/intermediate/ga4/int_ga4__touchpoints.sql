{{ config(
    materialized='view',
    enabled=var('ga4_export_enabled', false),
    tags=['ga4_intermediate']
) }}

{% set owned_hosts = var('ga4_owned_hosts', []) %}
{% set taxonomy = var('ga4_channel_taxonomy', []) %}

-- One touchpoint per (page event x session definition). Each row carries the
-- session key that belongs to its own definition: native under
-- native_session_key and custom-30m under custom_session_key, so the two never
-- collide and both definitions are directly comparable. Touchpoint precedence
-- and channel classification are configurable via ga4_owned_hosts /
-- ga4_channel_taxonomy; when unconfigured they remain null rather than being
-- guessed. Owned inheritance is intentionally a separate configured post-step
-- and is not inferred here.
with page_events as (
    select *
    from {{ ref('int_ga4__session_event_map_30m') }}
    where event_name in ('page_view', 'screen_view')
)
select
    p.observation_key as touchpoint_observation_key,
    'ga4_native' as session_definition,
    p.native_session_key as session_key,
    p.property_id,
    p.stream_id,
    p.user_pseudo_id,
    p.event_ts_utc as touchpoint_ts_utc,
    p.page_location,
    p.page_referrer,
    p.collected_manual_source as source_raw,
    p.collected_manual_medium as medium_raw,
    p.collected_manual_campaign as campaign_raw,
    p.collected_gclid,
    p.collected_dclid,
    p.collected_srsltid,
    p.analytics_storage_consent,
    p.ads_storage_consent,
    p.uses_transient_token,
    p.source_event_json,
    {{ ga4_touchpoint_precedence('p', owned_hosts) }} as touchpoint_precedence,
    {{ ga4_channel_group('p', taxonomy) }} as channel_group,
    cast(null as string) as owned_inheritance_status
from page_events p
where p.native_session_key is not null

union all

select
    p.observation_key as touchpoint_observation_key,
    'ga4_custom_30m' as session_definition,
    p.custom_session_key as session_key,
    p.property_id,
    p.stream_id,
    p.user_pseudo_id,
    p.event_ts_utc as touchpoint_ts_utc,
    p.page_location,
    p.page_referrer,
    p.collected_manual_source as source_raw,
    p.collected_manual_medium as medium_raw,
    p.collected_manual_campaign as campaign_raw,
    p.collected_gclid,
    p.collected_dclid,
    p.collected_srsltid,
    p.analytics_storage_consent,
    p.ads_storage_consent,
    p.uses_transient_token,
    p.source_event_json,
    {{ ga4_touchpoint_precedence('p', owned_hosts) }} as touchpoint_precedence,
    {{ ga4_channel_group('p', taxonomy) }} as channel_group,
    cast(null as string) as owned_inheritance_status
from page_events p
where p.custom_session_key is not null
