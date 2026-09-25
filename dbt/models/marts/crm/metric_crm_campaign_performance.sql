{{ config(materialized='view', tags=['crm']) }}
-- Delivery-cohort date, not interaction date. Unique metrics count deliveries,
-- not distinct people across multiple messages. All-history observed engagement.
with counts as (
    select shop_key, delivery_date, campaign_id, flow_id, message_id, message_category,
        count(*) as delivered_messages,
        countif(can_link_interactions) as linkable_messages,
        countif(not can_link_interactions) as unlinked_messages,
        countif(was_opened) as opened_messages,
        countif(was_clicked) as clicked_messages,
        sum(open_events) as open_events,
        sum(click_events) as click_events
    from {{ ref('fct_crm_message_engagement') }}
    group by shop_key, delivery_date, campaign_id, flow_id, message_id, message_category
)
select *,
    -- No false zero rates when keys are missing. Cube must use the components.
    case when unlinked_messages = 0 then safe_divide(opened_messages, delivered_messages) end as open_rate,
    case when unlinked_messages = 0 then safe_divide(clicked_messages, delivered_messages) end as click_through_rate,
    case when unlinked_messages = 0 then safe_divide(clicked_messages, opened_messages) end as click_to_open_rate
from counts
