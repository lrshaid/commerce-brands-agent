{{ config(tags=['business_intermediate']) }}
-- Reconciliation: int_shopify__shipping_lines vs stg_shopify__order_shipping_lines.
with staging as (
    select
        shop_key,
        extraction_id,
        count(*) as row_count,
        sum(original_price_shop_amount) as original_price_sum
    from {{ ref('stg_shopify__order_shipping_lines') }}
    group by shop_key, extraction_id
), typed as (
    select
        shop_key,
        extraction_id,
        count(*) as row_count,
        sum(original_price_shop_amount) as original_price_sum
    from {{ ref('int_shopify__shipping_lines') }}
    group by shop_key, extraction_id
)
select
    s.shop_key,
    s.extraction_id,
    s.row_count as staging_rows,
    t.row_count as typed_rows,
    s.original_price_sum as staging_original_price,
    t.original_price_sum as typed_original_price
from staging s
full outer join typed t
    on s.shop_key = t.shop_key and s.extraction_id = t.extraction_id
where
    s.row_count != t.row_count
    or s.original_price_sum != t.original_price_sum
