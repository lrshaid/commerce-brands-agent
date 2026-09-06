{{ config(tags=['shopify_typed']) }}
-- Reconciliation: typed_shopify__refunds vs flat refund staging models.
-- Grain is refund line item; transactions/adjustments are nested and must not
-- lose elements vs. their flat counterparts.
with staging_lines as (
    select
        shop_key,
        extraction_id,
        count(*) as row_count,
        sum(quantity) as quantity_sum,
        sum(subtotal_amount) as subtotal_sum,
        sum(total_tax_amount) as tax_sum
    from {{ ref('stg_shopify__refund_line_items') }}
    group by shop_key, extraction_id
), typed as (
    select
        shop_key,
        extraction_id,
        count(*) as row_count,
        sum(quantity) as quantity_sum,
        sum(subtotal_amount) as subtotal_sum,
        sum(total_tax_amount) as tax_sum,
        sum(array_length(transactions)) as transaction_count,
        sum(array_length(adjustments)) as adjustment_count
    from {{ ref('typed_shopify__refunds') }}
    group by shop_key, extraction_id
), flat_transactions as (
    select shop_key, extraction_id, count(*) as transaction_count from {{ ref('stg_shopify__refund_transactions') }} group by shop_key, extraction_id
), flat_adjustments as (
    select shop_key, extraction_id, count(*) as adjustment_count from {{ ref('stg_shopify__refund_adjustments') }} group by shop_key, extraction_id
)
select
    s.shop_key,
    s.extraction_id,
    s.row_count as staging_rows,
    t.row_count as typed_rows
from staging_lines s
full outer join typed t
    on s.shop_key = t.shop_key and s.extraction_id = t.extraction_id
left join flat_transactions ft on coalesce(s.shop_key, t.shop_key) = ft.shop_key and coalesce(s.extraction_id, t.extraction_id) = ft.extraction_id
left join flat_adjustments fa on coalesce(s.shop_key, t.shop_key) = fa.shop_key and coalesce(s.extraction_id, t.extraction_id) = fa.extraction_id
where
    s.row_count != t.row_count
    or s.quantity_sum != t.quantity_sum
    or s.subtotal_sum != t.subtotal_sum
    or s.tax_sum != t.tax_sum
    or coalesce(t.transaction_count, 0) != coalesce(ft.transaction_count, 0)
    or coalesce(t.adjustment_count, 0) != coalesce(fa.adjustment_count, 0)
