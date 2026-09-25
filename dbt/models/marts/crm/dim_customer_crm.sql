{{ config(materialized='view', tags=['crm']) }}
-- One CRM identity within a shop, including prospects. Hash identities may
-- combine multiple profiles only when their observed email mapping is unambiguous.
-- No consent claim: unsubscribe counts describe events, not current permission.
with engagement as (
    select shop_key, crm_person_key,
        max(customer_identity_id) as customer_identity_id,
        count(distinct profile_id) as profile_count,
        count(*) as total_events,
        min(event_ts) as first_event_ts,
        max(event_ts) as last_event_ts,
        max(case when event_type = 'clicked-email' then event_ts end) as last_email_click_ts,
        countif(event_type = 'received-email') as delivered_messages,
        countif(event_type = 'opened-email') as open_events,
        countif(event_type = 'clicked-email') as click_events,
        countif(event_type = 'bounced-email') as bounce_events,
        countif(event_type = 'unsubscribed-from-email-marketing') as unsubscribe_events,
        countif(event_type = 'clicked-email'
            and event_ts >= timestamp_sub(current_timestamp(), interval 30 day)
            and event_ts <= current_timestamp()) as click_events_30d,
        countif(event_type = 'clicked-email'
            and event_ts >= timestamp_sub(current_timestamp(), interval 90 day)
            and event_ts <= current_timestamp()) as click_events_90d,
        countif(identity_status = 'ambiguous_email') as ambiguous_identity_events
    from {{ ref('fct_crm_event') }}
    group by shop_key, crm_person_key
), orders as (
    select customer_identity_id, count(*) as order_count,
        min(order_ts) as first_purchase_ts, max(order_ts) as last_purchase_ts,
        count(distinct currency_code) as currency_count,
        countif(not value_is_complete) as incomplete_value_orders,
        min(currency_code) as currency_code, sum(order_value) as gross_spend
    from {{ ref('int_crm__order_value') }}
    where customer_identity_id is not null
    group by customer_identity_id
), refunds as (
    select o.customer_identity_id,
        sum(r.rmv_merchandise_amount) as recognized_rmv
    from {{ ref('fct_returns') }} r
    join {{ ref('int_crm__order_value') }} o
        on r.shop_key = o.shop_key and r.order_gid = o.order_gid
    where r.rmv_recognition_ts_utc is not null and o.customer_identity_id is not null
    group by o.customer_identity_id
), combined as (
    select e.*, coalesce(o.order_count, 0) as order_count,
        o.first_purchase_ts, o.last_purchase_ts,
        case when o.currency_count = 1 and o.incomplete_value_orders = 0 then o.currency_code end as currency_code,
        case when o.currency_count = 1 and o.incomplete_value_orders = 0 then o.gross_spend end as gross_spend,
        case when o.currency_count = 1 and o.incomplete_value_orders = 0 then coalesce(r.recognized_rmv, 0) end as recognized_rmv,
        case when e.customer_identity_id is null then 'Unresolved'
             when o.order_count > 0 then 'Customer' else 'Prospect' end as customer_status
    from engagement e
    left join orders o on e.customer_identity_id = o.customer_identity_id
    left join refunds r on e.customer_identity_id = r.customer_identity_id
)
select *, gross_spend + recognized_rmv as observed_net_value,
    timestamp_diff(current_timestamp(), last_email_click_ts, day) as days_since_email_click
from combined
