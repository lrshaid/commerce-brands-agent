{{ config(materialized='view', tags=['crm']) }}
-- One row per observed email delivery. Without a delivery token in the source,
-- associate interactions to the latest preceding delivery of the SAME profile
-- and message, stopping at the next delivery. This is an explicit heuristic.
with deliveries as (
    select *, lead(event_ts) over (
        partition by shop_key, profile_id, message_id
        order by event_ts, crm_event_key
    ) as next_delivery_ts
    from {{ ref('fct_crm_event') }}
    where event_type = 'received-email' and event_ts is not null
), interactions as (
    select crm_event_key, shop_key, profile_id, message_id, event_ts, event_type
    from {{ ref('fct_crm_event') }}
    where event_type in ('opened-email', 'clicked-email')
), engagement as (
    select d.crm_event_key as delivery_key,
        countif(i.event_type = 'opened-email') as open_events,
        countif(i.event_type = 'clicked-email') as click_events,
        min(case when i.event_type = 'opened-email' then i.event_ts end) as first_open_ts,
        min(case when i.event_type = 'clicked-email' then i.event_ts end) as first_click_ts,
        max(case when i.event_type = 'clicked-email' then i.event_ts end) as last_click_ts
    from deliveries d
    left join interactions i
        on d.shop_key = i.shop_key and d.profile_id = i.profile_id and d.message_id = i.message_id
        and i.event_ts >= d.event_ts
        and (d.next_delivery_ts is null or i.event_ts < d.next_delivery_ts)
    group by d.crm_event_key
)
select d.shop_key, d.crm_event_key as delivery_key, d.profile_id, d.crm_person_key,
    d.customer_identity_id, d.identity_status, d.message_id, d.campaign_id,
    d.campaign_name, d.flow_id, d.message_category, d.event_ts as delivery_ts,
    date(d.event_ts) as delivery_date, d.next_delivery_ts,
    d.profile_id is not null and d.message_id is not null as can_link_interactions,
    'latest_preceding_profile_message_delivery' as interaction_link_method,
    e.open_events, e.click_events, e.first_open_ts, e.first_click_ts, e.last_click_ts,
    e.open_events > 0 as was_opened, e.click_events > 0 as was_clicked
from deliveries d
join engagement e on d.crm_event_key = e.delivery_key
