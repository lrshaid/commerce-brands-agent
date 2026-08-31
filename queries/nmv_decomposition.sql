-- Expected input contract for the local NMV decomposition tool.
-- Replace {{project}} and {{dataset}} at deploy time.
select
    metric_date,
    sales_channel,
    gmv_amount,
    emv_amount,
    rmv_amount,
    nmv_amount,
    traffic,
    orders,
    units,
    safe_divide(orders, traffic) as cvr,
    safe_divide(units, orders) as upt,
    safe_divide(gmv_amount, units) as app,
    safe_divide(gmv_amount, orders) as aov
from `{{project}}.{{dataset}}.metric_revenue_daily`
where metric_date between @start_date and @end_date

