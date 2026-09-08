{{ config(
    materialized='view',
    enabled=var('ga4_export_enabled', false),
    tags=['business_marts']
) }}

{% set funnel_steps = var('ga4_funnel_steps', ['add_to_cart', 'begin_checkout', 'purchase']) %}
{% set purchase_in_steps = 'purchase' in funnel_steps %}

-- Digital funnel mart: sessions -> users -> each configured funnel step, by
-- day x channel. Session basis is the project's custom 30-minute session
-- (browser-scoped user_pseudo_id, no cross-device stitching). Channel comes
-- from the session's entry channel_group (ga4_channel_taxonomy); when no
-- taxonomy is configured it is null and surfaces as 'unclassified'. Each step
-- counts distinct sessions that emitted that event name at least once, so the
-- columns form a monotonic funnel. No revenue or financial policy is applied.
-- Single-shop assumption: property_id is the shop's GA4 property.
with sessions as (
    select
        property_id,
        stream_id,
        session_date_property as metric_date,
        custom_session_key as session_key,
        user_pseudo_id,
        coalesce(entry_channel_group, 'unclassified') as channel_group
    from {{ ref('int_ga4__sessions_30m') }}
),
session_events as (
    select
        s.property_id,
        s.stream_id,
        s.metric_date,
        s.channel_group,
        s.session_key,
        s.user_pseudo_id,
        m.event_name
    from sessions s
    join {{ ref('int_ga4__session_event_map_30m') }} m
        on m.custom_session_key = s.session_key
),
reached as (
    select
        property_id,
        stream_id,
        metric_date,
        channel_group,
        session_key,
        user_pseudo_id,
        {% for step in funnel_steps %}
        countif(event_name = '{{ step }}') > 0 as reached_{{ step }},
        {% endfor %}
        {% if not purchase_in_steps %}
        countif(event_name = 'purchase') > 0 as reached_purchase,
        {% endif %}
        count(*) as session_event_count
    from session_events
    group by property_id, stream_id, metric_date, channel_group, session_key, user_pseudo_id
)
select
    property_id,
    stream_id,
    metric_date,
    channel_group,
    count(*) as sessions,
    count(distinct user_pseudo_id) as users,
    {% for step in funnel_steps %}
    countif(reached_{{ step }}) as {{ step }}_sessions,
    {% endfor %}
    {% if not purchase_in_steps %}
    countif(reached_purchase) as purchase_sessions,
    {% endif %}
    current_timestamp() as computed_at
from reached
group by property_id, stream_id, metric_date, channel_group