-- metric_revenue_daily: NMV reconciles to GMV + EMV + RMV
-- EMV is coalesced to 0 because it is intentionally absent.
select *
from {{ ref('metric_revenue_daily') }}
where abs(nmv_amount - (gmv_amount + coalesce(emv_amount, 0) + rmv_amount)) > 0.0001
