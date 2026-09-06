{{ config(tags=['shopify_typed']) }}
-- Reconciliation: typed_shopify__orders vs stg_shopify__orders.
with staging as (
    select
        shop_key,
        extraction_id,
        count(*) as row_count,
        sum(total_price_shop_amount) as total_price_sum,
        sum(subtotal_price_shop_amount) as subtotal_sum,
        sum(total_tax_shop_amount) as tax_sum,
        sum(total_discounts_shop_amount) as discounts_sum
    from {{ ref('stg_shopify__orders') }}
    group by shop_key, extraction_id
), typed as (
    select
        shop_key,
        extraction_id,
        count(*) as row_count,
        sum(total_price_shop_amount) as total_price_sum,
        sum(subtotal_price_shop_amount) as subtotal_sum,
        sum(total_tax_shop_amount) as tax_sum,
        sum(total_discounts_shop_amount) as discounts_sum,
        sum(array_length(discount_applications)) as discount_app_count
    from {{ ref('typed_shopify__orders') }}
    group by shop_key, extraction_id
), flat_discounts as (
    select
        shop_key,
        extraction_id,
        count(*) as discount_app_count
    from {{ ref('stg_shopify__order_discount_applications') }}
    group by shop_key, extraction_id
)
select
    s.shop_key,
    s.extraction_id,
    s.row_count as staging_rows,
    t.row_count as typed_rows,
    s.total_price_sum as staging_total_price,
    t.total_price_sum as typed_total_price
from staging s
full outer join typed t
    on s.shop_key = t.shop_key and s.extraction_id = t.extraction_id
left join flat_discounts d
    on coalesce(s.shop_key, t.shop_key) = d.shop_key
    and coalesce(s.extraction_id, t.extraction_id) = d.extraction_id
where
    s.row_count != t.row_count
    or s.total_price_sum != t.total_price_sum
    or s.subtotal_sum != t.subtotal_sum
    or s.tax_sum != t.tax_sum
    or s.discounts_sum != t.discounts_sum
    or coalesce(t.discount_app_count, 0) != coalesce(d.discount_app_count, 0)
