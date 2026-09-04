select
    observation_key, shop_key, extraction_id, file_id, record_index,
    object_gid as line_item_gid, parent_gid as order_gid,
    cast(json_value(payload, '$.quantity') as int64) as quantity,
    json_value(payload, '$.sku') as sku,
    json_value(payload, '$.title') as title,
    json_value(payload, '$.variantTitle') as variant_title,
    json_value(payload, '$.product.id') as product_gid,
    json_value(payload, '$.variant.id') as variant_gid,
    cast(json_value(payload, '$.originalTotalSet.shopMoney.amount') as numeric) as original_total_shop_amount,
    json_value(payload, '$.originalTotalSet.shopMoney.currencyCode') as original_total_shop_currency,
    cast(json_value(payload, '$.discountedTotalSet.shopMoney.amount') as numeric) as discounted_total_shop_amount,
    json_value(payload, '$.discountedTotalSet.shopMoney.currencyCode') as discounted_total_shop_currency,
    extraction_started_at, extraction_completed_at, published_at, payload as original_payload
from {{ ref('stg_shopify__order_records') }}
where starts_with(object_gid, 'gid://shopify/LineItem/') and parent_gid is not null
