{{ config(tags=['intermediate_view']) }}
-- Aggregate refund line items to order-line grain before joining to returns.
-- This prevents the many-to-many fan-out when an original line has multiple
-- refund events and/or multiple return events.
select
    shop_key,
    extraction_id,
    order_gid,
    order_line_item_id,
    sum(quantity) as refunded_quantity,
    sum(subtotal_amount) as refund_subtotal_amount,
    sum(total_tax_amount) as refund_tax_amount,
    count(*) as refund_line_count,
    -- Recognition date: the refund created date. The refund is the atomic
-- recognition unit (its lines have no timestamp of their own and share the
-- header's created_at); when an original line is included in several separate
-- refunds, the order-line grain uses the latest refund created date.
    max(refund_created_at) as latest_refund_created_at
from {{ ref('int_shopify__refunds') }}
group by shop_key, extraction_id, order_gid, order_line_item_id
