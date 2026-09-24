select *
from {{ ref('metric_crm_campaign_performance') }}
where opened_messages > linkable_messages or clicked_messages > linkable_messages
   or delivered_messages != linkable_messages + unlinked_messages
   or opened_messages > open_events or clicked_messages > click_events
