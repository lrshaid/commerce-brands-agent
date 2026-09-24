-- No future touch; model-specific exact interval boundaries.
select *
from {{ ref('fct_crm_order_attribution') }}
where attribution_status = 'attributed'
  and (touch_event_key is null or seconds_to_order is null or seconds_to_order > 21600
       or seconds_to_order < case when attribution_model = 'delivery_6h' then 180 else 0 end)
