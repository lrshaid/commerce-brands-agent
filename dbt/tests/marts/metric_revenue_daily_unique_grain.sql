-- metric_revenue_daily: unique grain (shop_key, extraction_id, metric_date)
select shop_key, extraction_id, metric_date, count(*) as n
from {{ ref('metric_revenue_daily') }}
group by shop_key, extraction_id, metric_date
having count(*) > 1
