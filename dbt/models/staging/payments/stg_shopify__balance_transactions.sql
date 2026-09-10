{{ config(tags=['payments_staging']) }}
-- Balance transaction observations, one row per balance transaction edge.
with pages as (
    select * from {{ shopify_balance_transaction_pages() }}
)
select
    to_hex(sha256(to_json_string(struct(p.page_key, balance_offset)))) as observation_key,
    p.shop_key,
    p.extraction_id,
    p.page_key,
    json_value(e, '$.node.id') as balance_transaction_gid,
    json_value(e, '$.node.type') as type,
    cast(json_value(e, '$.node.test') as bool) as is_test,
    cast(json_value(e, '$.node.amount.amount') as numeric) as amount,
    json_value(e, '$.node.amount.currencyCode') as currency_code,
    cast(json_value(e, '$.node.fee.amount') as numeric) as fee_amount,
    cast(json_value(e, '$.node.net.amount') as numeric) as net_amount,
    cast(json_value(e, '$.node.transactionDate') as timestamp) as transaction_date,
    json_value(e, '$.node.associatedOrder.id') as order_gid,
    json_value(e, '$.node.associatedPayout.id') as payout_gid,
    json_value(e, '$.node.associatedPayout.status') as payout_status,
    p.captured_at,
    p.published_at
from pages p
cross join unnest(json_query_array(p.payload, '$.data.shopifyPaymentsAccount.balanceTransactions.edges')) e with offset balance_offset
