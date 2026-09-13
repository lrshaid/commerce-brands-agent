{{ config(tags=['refund_staging']) }}
-- Compare nested arrays for EACH line's parent; refunds with no lines remain in
-- flat staging. Summing repeated arrays across lines would double-count money.
with expected_transactions as (
    select shop_key, extraction_id, refund_gid, count(*) as n
    from {{ ref('stg_shopify__refund_transactions') }}
    group by shop_key, extraction_id, refund_gid
), expected_adjustments as (
    select shop_key, extraction_id, refund_gid, count(*) as n
    from {{ ref('stg_shopify__refund_adjustments') }}
    group by shop_key, extraction_id, refund_gid
), lines as (
    select * from {{ ref('stg_shopify__refund_line_items') }}
), nested as (
    select * from {{ ref('int_shopify__refunds') }}
)
select coalesce(l.observation_key, n.observation_key) as observation_key
from lines l
full outer join nested n on l.observation_key = n.observation_key
left join expected_transactions t
    on l.shop_key = t.shop_key and l.extraction_id = t.extraction_id and l.refund_gid = t.refund_gid
left join expected_adjustments a
    on l.shop_key = a.shop_key and l.extraction_id = a.extraction_id and l.refund_gid = a.refund_gid
where l.observation_key is null or n.observation_key is null
    or l.quantity is distinct from n.quantity
    or l.subtotal_amount is distinct from n.subtotal_amount
    or l.total_tax_amount is distinct from n.total_tax_amount
    or array_length(n.transactions) != coalesce(t.n, 0)
    or array_length(n.adjustments) != coalesce(a.n, 0)
