-- Revenue-core mart. The EMV business rule is documented, but its raw exchange-line contract is still missing.
with gmv as (
    select
        date(processed_at) as metric_date,
        sum(net_merchandise_amount) as gmv_amount
    from `{{project}}.{{dataset}}.stg_order_line_items`
    group by metric_date
)
, rmv as (
    select
        date(recognized_at) as metric_date,
        sum(rmv_merchandise_amount) as rmv_amount
    from `{{project}}.{{dataset}}.fct_returns`
    group by metric_date
)
select
    coalesce(g.metric_date, r.metric_date) as metric_date,
    'all' as sales_channel,
    coalesce(g.gmv_amount, 0) as gmv_amount,
    cast(null as numeric) as emv_amount,
    coalesce(r.rmv_amount, 0) as rmv_amount,
    cast(null as numeric) as nmv_amount,
    cast(null as int64) as traffic,
    cast(null as int64) as orders,
    cast(null as int64) as units
from gmv g
full outer join rmv r using (metric_date)
