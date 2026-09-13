{{ config(tags=['order_transactions_reconciliation'], severity='warn') }}
-- Diagnostic only: independently observed API states can differ. Compare overlapping
-- IDs; do not require refunds to contain every non-refund order transaction.
with refunds as (
    select *
    from {{ ref('stg_shopify__refund_transactions') }}
    qualify row_number() over (
        partition by shop_key, transaction_gid order by captured_at desc, extraction_id desc, observation_key desc
    ) = 1
), transactions as (
    select * from {{ ref('int_shopify__order_transactions') }}
)
select r.shop_key, r.transaction_gid, r.captured_at as refund_captured_at, t.captured_at as order_captured_at
from refunds r
join transactions t using (shop_key, transaction_gid)
where r.amount is distinct from t.amount
    or r.currency_code is distinct from t.currency_code
    or r.presentment_amount is distinct from t.presentment_amount
    or r.presentment_currency_code is distinct from t.presentment_currency_code
    or r.order_gid is distinct from t.order_gid
