{{ config(tags=['order_transactions_staging']) }}
with published as (
    select r.shop_key, r.extraction_id, r.payload
    from {{ source('shopify_order_transactions', 'order_transactions') }} r
    join {{ source('shopify_order_transactions', 'ingestion_runs') }} m
        on r.shop_key = m.shop_key and r.extraction_id = m.extraction_id
        and m.stream = 'order_transactions' and m.status = 'published'
), expected as (
    select shop_key, extraction_id, sum(array_length(json_query_array(payload, '$.transactions'))) as n
    from published group by shop_key, extraction_id
), actual as (
    select shop_key, extraction_id, count(*) as n
    from {{ ref('stg_shopify__order_transactions') }} group by shop_key, extraction_id
)
select e.shop_key, e.extraction_id
from expected e
left join actual a using (shop_key, extraction_id)
where e.n != coalesce(a.n, 0)
