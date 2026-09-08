{{ config(tags=['products_staging']) }}
select
    to_hex(sha256(to_json_string(struct(r.shop_key, r.extraction_id, r.file_id, r.record_index)))) as page_key,
    r.shop_key, r.extraction_id, r.file_id, r.record_index, r.record_sha256,
    r.record_text, r.ingested_at, m.published_at,
    json_value(f, '$.variables.id') as product_gid,
    r.payload
from {{ source('shopify_products', 'variants') }} r
join {{ source('shopify_products', 'ingestion_runs') }} m
  on r.shop_key = m.shop_key and r.extraction_id = m.extraction_id
 and m.stream = 'variants' and m.status = 'published'
 and m.transport = 'shopify_graphql_pages'
cross join unnest(json_query_array(m.files)) f
where json_value(f, '$.role') = 'response_page'
  and json_value(f, '$.generation') = r.file_id
  and json_value(f, '$.sha256') = r.record_sha256
