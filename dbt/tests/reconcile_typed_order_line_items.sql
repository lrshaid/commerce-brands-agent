{{ config(tags=['shopify_typed']) }}
-- Reconciliation: typed_shopify__order_line_items vs stg_shopify__order_line_items.
with staging as (
    select
        shop_key,
        extraction_id,
        count(*) as row_count,
        sum(original_total_shop_amount) as original_total_sum,
        sum(discounted_total_shop_amount) as discounted_total_sum
    from {{ ref('stg_shopify__order_line_items') }}
    group by shop_key, extraction_id
), typed as (
    select
        shop_key,
        extraction_id,
        count(*) as row_count,
        sum(original_total_shop_amount) as original_total_sum,
        sum(discounted_total_shop_amount) as discounted_total_sum,
        sum(array_length(discount_allocations)) as discount_alloc_count
    from {{ ref('typed_shopify__order_line_items') }}
    group by shop_key, extraction_id
), flat_discounts as (
    select
        shop_key,
        extraction_id,
        count(*) as discount_alloc_count
    from {{ ref('stg_shopify__order_discount_allocations') }}
    group by shop_key, extraction_id
)
select
    s.shop_key,
    s.extraction_id,
    s.row_count as staging_rows,
    t.row_count as typed_rows,
    s.discounted_total_sum as staging_discounted_total,
    t.discounted_total_sum as typed_discounted_total
from staging s
full outer join typed t
    on s.shop_key = t.shop_key and s.extraction_id = t.extraction_id
left join flat_discounts d
    on coalesce(s.shop_key, t.shop_key) = d.shop_key
    and coalesce(s.extraction_id, t.extraction_id) = d.extraction_id
where
    s.row_count != t.row_count
    or s.original_total_sum != t.original_total_sum
    or s.discounted_total_sum != t.discounted_total_sum
    or coalesce(t.discount_alloc_count, 0) != coalesce(d.discount_alloc_count, 0)
