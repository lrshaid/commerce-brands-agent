{{ config(tags=['shopify_typed']) }}
-- Reconciliation: typed_shopify__return_line_items vs flat return staging models.
with staging_lines as (
    select
        shop_key,
        extraction_id,
        count(*) as row_count,
        sum(quantity) as quantity_sum,
        sum(subtotal_amount) as subtotal_sum,
        sum(total_tax_amount) as tax_sum
    from {{ ref('stg_shopify__return_line_items') }}
    group by shop_key, extraction_id
), typed as (
    select
        shop_key,
        extraction_id,
        count(*) as row_count,
        sum(quantity) as quantity_sum,
        sum(subtotal_amount) as subtotal_sum,
        sum(total_tax_amount) as tax_sum,
        sum(array_length(return_refunds)) as return_refund_count
    from {{ ref('typed_shopify__return_line_items') }}
    group by shop_key, extraction_id
), flat_refunds as (
    select shop_key, extraction_id, count(*) as return_refund_count from {{ ref('stg_shopify__return_refunds') }} group by shop_key, extraction_id
)
select
    s.shop_key,
    s.extraction_id,
    s.row_count as staging_rows,
    t.row_count as typed_rows
from staging_lines s
full outer join typed t
    on s.shop_key = t.shop_key and s.extraction_id = t.extraction_id
left join flat_refunds fr on coalesce(s.shop_key, t.shop_key) = fr.shop_key and coalesce(s.extraction_id, t.extraction_id) = fr.extraction_id
where
    s.row_count != t.row_count
    or s.quantity_sum != t.quantity_sum
    or s.subtotal_sum != t.subtotal_sum
    or s.tax_sum != t.tax_sum
    or coalesce(t.return_refund_count, 0) != coalesce(fr.return_refund_count, 0)
