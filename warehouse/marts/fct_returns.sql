-- Full-outer return/refund fact. Refund values win when both sides exist.
with refund_lines as (
    select
        order_id,
        order_line_item_id,
        refund_line_item_id,
        refund_created_at,
        subtotal_amount,
        tax_amount
    from `{{project}}.{{dataset}}.stg_refund_line_items`
)
, return_lines as (
    select
        order_id,
        order_line_item_id,
        return_line_item_id,
        return_created_at,
        subtotal_amount,
        tax_amount
    from `{{project}}.{{dataset}}.stg_return_line_items`
)
select
    coalesce(rf.order_id, rt.order_id) as order_id,
    coalesce(rf.order_line_item_id, rt.order_line_item_id) as order_line_item_id,
    rf.refund_line_item_id,
    rt.return_line_item_id,
    coalesce(rf.refund_created_at, rt.return_created_at) as recognized_at,
    case
        when rf.refund_line_item_id is not null and rt.return_line_item_id is not null
            then 'matched'
        when rf.refund_line_item_id is not null then 'refund_no_return'
        else 'return_no_refund'
    end as match_status,
    -abs(coalesce(rf.subtotal_amount, rt.subtotal_amount, 0)) as rmv_merchandise_amount,
    -abs(coalesce(rf.tax_amount, rt.tax_amount, 0)) as rmv_tax_amount
from refund_lines rf
full outer join return_lines rt
    on rf.order_line_item_id = rt.order_line_item_id

