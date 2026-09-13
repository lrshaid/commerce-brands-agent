{{ config(tags=['order_transactions_staging']) }}
with records as (
    select r.*, m.published_at,
        to_hex(sha256(to_json_string(struct(r.shop_key, r.extraction_id, r.file_id, r.record_index)))) as record_key
    from {{ source('shopify_order_transactions', 'order_transactions') }} r
    join {{ source('shopify_order_transactions', 'ingestion_runs') }} m
        on r.shop_key = m.shop_key and r.extraction_id = m.extraction_id
        and m.stream = 'order_transactions' and m.status = 'published'
        and m.transport = 'shopify_bulk_query'
), transactions as (
    select r.*, n as node_payload, node_offset
    from records r
    cross join unnest(json_query_array(r.payload, '$.transactions')) n with offset node_offset
)
select
    to_hex(sha256(to_json_string(struct(c.record_key, c.node_offset)))) as observation_key,
    c.shop_key, c.extraction_id, c.ingested_at as captured_at, c.published_at,
    json_value(c.payload, '$.id') as order_gid,
    cast(json_value(c.payload, '$.updatedAt') as timestamp) as order_updated_at,
    json_value(c.node_payload, '$.id') as transaction_gid,
    json_value(c.node_payload, '$.kind') as kind,
    json_value(c.node_payload, '$.status') as status,
    cast(json_value(c.node_payload, '$.amountSet.shopMoney.amount') as numeric) as amount,
    json_value(c.node_payload, '$.amountSet.shopMoney.currencyCode') as currency_code,
    cast(json_value(c.node_payload, '$.amountSet.presentmentMoney.amount') as numeric) as presentment_amount,
    json_value(c.node_payload, '$.amountSet.presentmentMoney.currencyCode') as presentment_currency_code,
    json_value(c.node_payload, '$.paymentId') as payment_id,
    json_value(c.node_payload, '$.parentTransaction.id') as parent_transaction_gid,
    json_value(c.node_payload, '$.gateway') as gateway,
    json_value(c.node_payload, '$.formattedGateway') as formatted_gateway,
    cast(json_value(c.node_payload, '$.createdAt') as timestamp) as created_at,
    cast(json_value(c.node_payload, '$.processedAt') as timestamp) as processed_at,
    cast(json_value(c.node_payload, '$.test') as bool) as is_test,
    json_value(c.node_payload, '$.errorCode') as error_code,
    json_query(c.node_payload, '$.receiptJson') as receipt,
    json_value(c.node_payload, '$.settlementCurrency') as settlement_currency,
    cast(json_value(c.node_payload, '$.settlementCurrencyRate') as numeric) as settlement_currency_rate,
    cast(json_value(c.node_payload, '$.multiCapturable') as bool) as multi_capturable,
    cast(json_value(c.node_payload, '$.manuallyCapturable') as bool) as manually_capturable,
    json_query(c.node_payload, '$.maximumRefundableV2') as maximum_refundable,
    json_value(c.node_payload, '$.location.id') as location_gid,
    json_value(c.node_payload, '$.location.name') as location_name,
    json_value(c.node_payload, '$.user.id') as user_gid,
    json_value(c.node_payload, '$.device.id') as device_gid,
    json_query(c.node_payload, '$.paymentDetails') as payment_details,
    json_query(c.node_payload, '$.fees') as fees,
    json_value(c.node_payload, '$.shopifyPaymentsSet.refundSet.acquirerReferenceNumber') as acquirer_reference_number,
    c.node_payload as detail_payload
from transactions c
