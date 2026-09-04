select
    observation_key, shop_key, extraction_id, file_id, record_index,
    object_gid as shipping_line_gid, parent_gid as order_gid,
    json_value(payload, '$.title') as title,
    json_value(payload, '$.code') as code,
    cast(json_value(payload, '$.originalPriceSet.shopMoney.amount') as numeric) as original_price_shop_amount,
    json_value(payload, '$.originalPriceSet.shopMoney.currencyCode') as original_price_shop_currency,
    extraction_started_at, extraction_completed_at, published_at, payload as original_payload
from {{ ref('stg_shopify__order_records') }}
where starts_with(object_gid, 'gid://shopify/ShippingLine/') and parent_gid is not null
