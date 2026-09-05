{{ config(tags=['business_marts']) }}
-- Revenue-core mart. GMV/EMV/RMV/NMV by day.
-- EMV is intentionally absent (NULL/0) because the exchange-line contract
-- (exchangeV2s) is invalid; see warehouse/contracts/decisions.yaml.
-- RMV is stored negative so NMV = GMV + EMV + RMV by simple addition.
with gmv as (
    select
        shop_key,
        extraction_id,
        date(processed_at) as metric_date,
        sum(discounted_total_shop_amount) as gmv_amount,
        count(distinct order_gid) as gmv_orders,
        sum(quantity) as gmv_units
    from {{ ref('stg_shopify__orders') }} o
    join {{ ref('stg_shopify__order_line_items') }} l
        on o.shop_key = l.shop_key
        and o.extraction_id = l.extraction_id
        and o.order_gid = l.order_gid
    where o.processed_at is not null
    group by shop_key, extraction_id, date(processed_at)
)
, rmv as (
    select
        shop_key,
        extraction_id,
        date(current_timestamp()) as metric_date,
        sum(rmv_merchandise_amount) as rmv_amount,
        sum(returned_quantity) as rmv_units
    from {{ ref('fct_returns') }}
    group by shop_key, extraction_id, date(current_timestamp())
)
select
    coalesce(g.shop_key, r.shop_key) as shop_key,
    coalesce(g.extraction_id, r.extraction_id) as extraction_id,
    coalesce(g.metric_date, r.metric_date) as metric_date,
    'all' as sales_channel,
    coalesce(g.gmv_amount, 0) as gmv_amount,
    -- EMV intentionally absent until a valid exchange contract exists.
    cast(null as numeric) as emv_amount,
    coalesce(r.rmv_amount, 0) as rmv_amount,
    coalesce(g.gmv_amount, 0) + coalesce(r.rmv_amount, 0) as nmv_amount,
    cast(null as int64) as traffic,
    coalesce(g.gmv_orders, 0) as orders,
    coalesce(g.gmv_units, 0) as gross_units,
    coalesce(r.rmv_units, 0) as returned_units,
    coalesce(g.gmv_units, 0) + coalesce(r.rmv_units, 0) as net_units,
    current_timestamp() as computed_at
from gmv g
full outer join rmv r
    on g.shop_key = r.shop_key
    and g.extraction_id = r.extraction_id
    and g.metric_date = r.metric_date
