-- Discount allocations per order line, parsed from the line item payload.
-- The active orders_bulk.graphql query does NOT request discountAllocations,
-- so this view currently returns zero rows; it is retained for forward-compatibility.
select
    observation_key,
    shop_key,
    extraction_id,
    file_id,
    record_index,
    object_gid as line_item_gid,
    parent_gid as order_gid,
    cast(json_value(da, '$.allocatedAmountSet.shopMoney.amount') as numeric) as allocated_amount_shop_amount,
    json_value(da, '$.allocatedAmountSet.shopMoney.currencyCode') as allocated_amount_shop_currency,
    cast(json_value(da, '$.discountApplication.index') as int64) as discount_application_index,
    json_value(da, '$.discountApplication.targetType') as target_type,
    json_value(da, '$.discountApplication.allocationMethod') as allocation_method,
    json_value(da, '$.discountApplication.targetSelection') as target_selection,
    extraction_started_at,
    extraction_completed_at,
    published_at,
    payload as original_payload
from {{ ref('stg_shopify__order_records') }}
cross join unnest(coalesce(json_query_array(payload, '$.discountAllocations'), [])) as da
where starts_with(object_gid, 'gid://shopify/LineItem/') and parent_gid is not null
