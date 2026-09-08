{{ config(tags=['products_staging']) }}
select
    to_hex(sha256(to_json_string(struct(p.page_key, product_offset)))) as observation_key,
    p.shop_key, p.extraction_id, p.page_key,
    json_value(node, '$.id') as product_gid,
    json_value(node, '$.title') as title,
    json_value(node, '$.productType') as product_type,
    json_value(node, '$.vendor') as vendor,
    cast(json_value(node, '$.createdAt') as timestamp) as created_at,
    cast(json_value(node, '$.updatedAt') as timestamp) as updated_at,
    p.ingested_at, p.published_at
from {{ ref('stg_shopify__product_pages') }} p
cross join unnest(json_query_array(p.payload, '$.data.products.nodes')) node with offset product_offset
