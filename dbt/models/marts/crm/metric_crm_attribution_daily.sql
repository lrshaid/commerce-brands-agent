{{ config(materialized='view', tags=['crm']) }}
-- Purchase-date reporting. Never sum attribution models or currencies.
select shop_key, order_date, attribution_model, currency_code, campaign_id, flow_id,
    message_id, attribution_status,
    count(*) as orders,
    countif(attribution_status = 'attributed') as attributed_orders,
    countif(attribution_status = 'attributed' and not value_is_complete) as missing_value_orders,
    case when countif(attribution_status = 'attributed' and not value_is_complete) = 0
         then sum(coalesce(attributed_value, 0)) end as attributed_value
from {{ ref('fct_crm_order_attribution') }}
group by shop_key, order_date, attribution_model, currency_code, campaign_id, flow_id, message_id, attribution_status
