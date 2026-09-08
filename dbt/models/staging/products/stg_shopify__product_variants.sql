{{ config(tags=['products_staging']) }}
select
    to_hex(sha256(to_json_string(struct(p.page_key, variant_offset)))) as observation_key,
    p.shop_key, p.extraction_id, p.page_key,
    p.product_gid,
    json_value(variant, '$.id') as variant_gid,
    json_value(variant, '$.sku') as sku,
    cast(json_value(variant, '$.price') as numeric) as price,
    cast(json_value(variant, '$.inventoryQuantity') as int64) as inventory_quantity,
    json_value(variant, '$.inventoryItem.id') as inventory_item_gid,
    p.ingested_at, p.published_at
from {{ ref('stg_shopify__variant_pages') }} p
cross join unnest(json_query_array(p.payload, '$.data.node.variants.nodes')) variant with offset variant_offset
