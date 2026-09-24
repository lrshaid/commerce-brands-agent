{{ config(materialized='view', tags=['crm']) }}
-- Current CRM population summary. No raw email or per-profile export.
select shop_key, customer_status, currency_code,
    count(*) as crm_identities,
    sum(delivered_messages) as delivered_messages,
    sum(open_events) as open_events,
    sum(click_events) as click_events,
    sum(click_events_30d) as click_events_30d,
    sum(click_events_90d) as click_events_90d,
    countif(click_events_30d > 0) as identities_clicked_30d,
    sum(order_count) as orders,
    sum(gross_spend) as gross_spend,
    sum(recognized_rmv) as recognized_rmv,
    sum(observed_net_value) as observed_net_value
from {{ ref('dim_customer_crm') }}
group by shop_key, customer_status, currency_code
