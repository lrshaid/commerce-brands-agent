{{ config(
    materialized='view',
    enabled=var('ga4_export_enabled', false),
    tags=['ga4_staging']
) }}

{% if var('ga4_export_enabled', false) and (
    not var('ga4_project', '') or
    not var('ga4_dataset', '') or
    not var('ga4_property_id', '') or
    not var('ga4_start_date', '') or
    not var('ga4_end_date', '')
) %}
    {{ exceptions.raise_compiler_error('GA4 enabled requires ga4_project, ga4_dataset, ga4_property_id, ga4_start_date and ga4_end_date') }}
{% endif %}

-- One row per source GA4 event.  This is a source adapter, not a tracker
-- session model: user_pseudo_id and ga_session_id are never replaced by a
-- synthetic or cross-device identity.
-- Event params are extracted through named CTEs (unnest + pivot) instead of
-- scalar subqueries. Duplicate param keys (invalid in practice) resolve to the
-- max value rather than an arbitrary first match.
with source_events as (
    select
        row_number() over () as source_row_index,
        _table_suffix as export_table_suffix,
        e,
        to_json_string(e) as source_event_json
    from {{ source('ga4_export', 'events') }} as e
    where regexp_contains(_table_suffix, r'^[0-9]{8}$')
      {% if var('ga4_export_enabled', false) %}
      and _table_suffix between '{{ var('ga4_start_date') }}' and '{{ var('ga4_end_date') }}'
      {% endif %}
),
event_params as (
    select
        s.source_row_index,
        p.key as param_key,
        p.value.string_value as param_string_value,
        p.value.int_value as param_int_value
    from source_events s,
    unnest(s.e.event_params) as p
),
param_values as (
    select
        source_row_index,
        max(if(param_key = 'ga_session_id', param_int_value, null)) as ga_session_id,
        max(if(param_key = 'transaction_id', param_string_value, null)) as transaction_id_param,
        max(if(param_key = 'page_location', param_string_value, null)) as page_location_param,
        max(if(param_key = 'page_referrer', param_string_value, null)) as page_referrer_param,
        max(if(param_key = 'page_title', param_string_value, null)) as page_title_param
    from event_params
    group by source_row_index
),
projected as (
    select
        cast('{{ var('ga4_property_id', '') }}' as string) as property_id,
        s.export_table_suffix,
        to_hex(sha256(to_json_string(struct(
            '{{ var('ga4_property_id', '') }}' as property_id,
            s.export_table_suffix,
            s.e.event_date,
            s.e.event_timestamp,
            s.e.event_name,
            s.e.user_pseudo_id,
            s.e.stream_id,
            s.e.event_bundle_sequence_id,
            s.e.batch_event_index,
            s.e.batch_ordering_id,
            s.e.batch_page_id,
            s.source_event_json
        )))) as observation_key,
        s.e.event_date as event_date_property,
        safe.parse_date('%Y%m%d', s.e.event_date) as event_date_parsed,
        timestamp_micros(s.e.event_timestamp) as event_ts_utc,
        s.e.event_timestamp as event_timestamp_micros,
        s.e.event_name,
        s.e.batch_event_index,
        s.e.batch_ordering_id,
        s.e.batch_page_id,
        s.e.event_bundle_sequence_id,
        s.e.user_pseudo_id,
        s.e.user_id,
        cast(s.e.stream_id as string) as stream_id,
        safe_cast(v.ga_session_id as int64) as ga_session_id,
        coalesce(
            s.e.ecommerce.transaction_id,
            v.transaction_id_param
        ) as transaction_id,
        s.e.ecommerce.purchase_revenue as ecommerce_purchase_revenue,
        s.e.ecommerce.purchase_revenue_in_usd as ecommerce_purchase_revenue_in_usd,
        v.page_location_param as page_location,
        v.page_referrer_param as page_referrer,
        v.page_title_param as page_title,
        s.e.privacy_info.analytics_storage as analytics_storage_consent,
        s.e.privacy_info.ads_storage as ads_storage_consent,
        s.e.privacy_info.uses_transient_token,
        s.e.collected_traffic_source.manual_source as collected_manual_source,
        s.e.collected_traffic_source.manual_medium as collected_manual_medium,
        s.e.collected_traffic_source.manual_campaign_name as collected_manual_campaign,
        s.e.collected_traffic_source.gclid as collected_gclid,
        s.e.collected_traffic_source.dclid as collected_dclid,
        s.e.collected_traffic_source.srsltid as collected_srsltid,
        -- Keep the nested source record intact; attribution policy belongs in
        -- a later model and must not be inferred in this adapter.
        to_json_string(s.e.collected_traffic_source) as collected_traffic_source_json,
        -- JSON extraction keeps this optional nested field tolerant of older
        -- export schemas while preserving it when the source supplies it.
        json_query(s.source_event_json, '$.session_traffic_source_last_click') as session_traffic_source_json,
        s.source_event_json
    from source_events s
    left join param_values v
        on s.source_row_index = v.source_row_index
)
select
    property_id,
    export_table_suffix,
    observation_key,
    event_date_property,
    event_date_parsed,
    event_ts_utc,
    event_timestamp_micros,
    event_name,
    batch_event_index,
    batch_ordering_id,
    batch_page_id,
    event_bundle_sequence_id,
    user_pseudo_id,
    user_id,
    stream_id,
    ga_session_id,
    transaction_id,
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
    property_id is null or property_id = '' as missing_property_id,
    user_pseudo_id is null or user_pseudo_id = '' as missing_user_pseudo_id,
    ga_session_id is null as missing_ga_session_id,
    (event_name = 'purchase' and (transaction_id is null or transaction_id = ''))
        as missing_purchase_transaction_id,
    case
        when nullif(property_id, '') is null
            or nullif(stream_id, '') is null
            or nullif(user_pseudo_id, '') is null
            or ga_session_id is null
        then null
        else concat(property_id, ':', stream_id, ':', user_pseudo_id, ':', cast(ga_session_id as string))
    end as native_session_key
from projected