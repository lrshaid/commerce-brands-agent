{{ config(materialized='view', tags=['crm']) }}
-- One order per rule, including unmatched orders. One touch can win for
-- multiple orders; never deduplicate the joined candidates by event ID.
with rules as (
    select 'delivery_6h' as attribution_model, 'received-email' as event_type, 180 as min_seconds, 21600 as max_seconds
    union all
    select 'click_6h', 'clicked-email', 0, 21600
), order_rules as (
    select o.*, r.attribution_model, r.event_type as eligible_event_type, r.min_seconds, r.max_seconds
    from {{ ref('int_crm__order_value') }} o
    cross join rules r
), candidates as (
    select o.*, e.crm_event_key, e.event_ts as touch_ts, e.campaign_id, e.flow_id, e.message_id,
        e.message_category, e.profile_id,
        timestamp_diff(o.order_ts, e.event_ts, second) as seconds_to_order,
        row_number() over (
            partition by o.shop_key, o.order_gid, o.attribution_model
            order by e.event_ts desc nulls last, e.crm_event_key desc
        ) as touch_rank
    from order_rules o
    left join {{ ref('fct_crm_event') }} e
        on o.customer_identity_id = e.customer_identity_id
        and e.event_type = o.eligible_event_type
        and e.event_ts >= timestamp_sub(o.order_ts, interval o.max_seconds second)
        and e.event_ts <= timestamp_sub(o.order_ts, interval o.min_seconds second)
)
select shop_key, order_gid, attribution_model, order_ts, order_date, customer_identity_id,
    order_value, currency_code, value_is_complete, crm_event_key as touch_event_key, touch_ts,
    date(touch_ts) as touch_date, seconds_to_order, campaign_id, flow_id, message_id,
    message_category, profile_id,
    case when crm_event_key is not null then 'attributed'
         when customer_identity_id is null then 'missing_order_email'
         else 'no_eligible_touch' end as attribution_status,
    case when crm_event_key is not null then order_value end as attributed_value
from candidates
where touch_rank = 1
