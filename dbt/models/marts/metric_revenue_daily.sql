{{ config(tags=['business_marts']) }}
-- Revenue-core mart. GMV/EMV/RMV/NMV by day.
-- EMV is intentionally absent (NULL/0) because the exchange-line contract
-- (exchangeV2s) is invalid; see warehouse/contracts/decisions.yaml.
-- RMV is stored negative so NMV = GMV + EMV + RMV by simple addition.
-- RMV is recognized on the associated refund created date (refund is the main
-- source of line items); returns without a refund are not recognized yet.
-- Cancelled orders are excluded from GMV and from their own refunds in RMV;
-- amounts are discounted (post-promotion) totals, i.e. net value.
with gmv as (
    select
        o.shop_key,
        o.extraction_id,
        date(o.processed_at) as metric_date,
        -- Cancelled orders contribute nothing: CASE inside the sum so the
        -- joined rows stay visible while only valid orders count. Amounts are
        -- discounted (post-promotion) totals, i.e. already net value.
        sum(case when o.cancelled_at is not null then 0 else l.discounted_total_shop_amount end) as gmv_amount,
        count(distinct case when o.cancelled_at is null then o.order_gid end) as gmv_orders,
        sum(case when o.cancelled_at is not null then 0 else l.quantity end) as gmv_units
    from {{ ref('int_shopify__orders') }} o
    join {{ ref('int_shopify__order_line_items') }} l
        on o.shop_key = l.shop_key
        and o.extraction_id = l.extraction_id
        and o.order_gid = l.order_gid
    where o.processed_at is not null
    group by o.shop_key, o.extraction_id, date(o.processed_at)
)
, rmv as (
    select
        r.shop_key,
        r.extraction_id,
        date(r.rmv_recognition_ts_utc) as metric_date,
        -- Refunds of cancelled orders are cancellation of the sale (the sale
        -- never entered GMV), so they are excluded to keep NMV consistent.
        sum(case when o.cancelled_at is not null then 0 else r.rmv_merchandise_amount end) as rmv_amount,
        sum(case when o.cancelled_at is not null then 0 else r.returned_quantity end) as rmv_units
    from {{ ref('fct_returns') }} r
    join {{ ref('int_shopify__orders') }} o
        on r.shop_key = o.shop_key
        and r.extraction_id = o.extraction_id
        and r.order_gid = o.order_gid
    where r.rmv_recognition_ts_utc is not null
    group by r.shop_key, r.extraction_id, date(r.rmv_recognition_ts_utc)
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
