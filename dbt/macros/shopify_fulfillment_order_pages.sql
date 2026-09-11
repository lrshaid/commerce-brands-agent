{% macro shopify_fulfillment_order_pages(stream) %}
(
  select
    to_hex(sha256(to_json_string(struct(r.shop_key, r.extraction_id, r.file_id, r.record_index)))) as page_key,
    r.shop_key,
    r.extraction_id,
    r.file_id,
    json_value(f, '$.operation') as operation,
    json_value(f, '$.variables.id') as owner_gid,
    cast(json_value(f, '$.captured_at') as timestamp) as captured_at,
    m.window_start,
    m.window_end,
    m.published_at,
    r.payload
  from {{ source('shopify_fulfillment_orders', stream) }} r
  join {{ source('shopify_fulfillment_orders', 'ingestion_runs') }} m
    on r.shop_key = m.shop_key
    and r.extraction_id = m.extraction_id
    and m.stream = '{{ stream }}'
    and m.status = 'published'
    and m.transport = 'shopify_graphql_pages'
  cross join unnest(json_query_array(m.files)) f
  where json_value(f, '$.role') = 'response_page'
    and json_value(f, '$.generation') = r.file_id
    and json_value(f, '$.sha256') = r.record_sha256
)
{% endmacro %}
