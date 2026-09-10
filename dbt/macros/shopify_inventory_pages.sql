{#
  Parse raw inventory HTTP pages into page-level helpers.  Inventory items live
  in their own raw table; inventory levels share one table between the location
  root pages and the owner-scoped level pages, so this macro keeps the
  operation and owning location.
#}
{% macro shopify_inventory_item_pages() %}
(
  select
    to_hex(sha256(to_json_string(struct(r.shop_key, r.extraction_id, r.file_id, r.record_index)))) as page_key,
    r.shop_key,
    r.extraction_id,
    r.file_id,
    r.record_index,
    r.record_sha256,
    r.record_text,
    r.ingested_at,
    cast(json_value(f, '$.captured_at') as timestamp) as captured_at,
    m.published_at,
    r.payload
  from {{ source('shopify_inventory', 'inventory_items') }} r
  join {{ source('shopify_inventory', 'ingestion_runs') }} m
    on r.shop_key = m.shop_key
    and r.extraction_id = m.extraction_id
    and m.stream = 'inventory_items'
    and m.status = 'published'
    and m.transport = 'shopify_graphql_pages'
  cross join unnest(json_query_array(m.files)) f
  where json_value(f, '$.role') = 'response_page'
    and json_value(f, '$.generation') = r.file_id
    and json_value(f, '$.sha256') = r.record_sha256
)
{% endmacro %}
{% macro shopify_inventory_level_pages() %}
(
  select
    to_hex(sha256(to_json_string(struct(r.shop_key, r.extraction_id, r.file_id, r.record_index)))) as page_key,
    r.shop_key,
    r.extraction_id,
    r.file_id,
    r.record_index,
    r.record_sha256,
    r.record_text,
    r.ingested_at,
    json_value(f, '$.operation') as operation,
    json_value(f, '$.variables.id') as owner_gid,
    cast(json_value(f, '$.captured_at') as timestamp) as captured_at,
    m.published_at,
    r.payload
  from {{ source('shopify_inventory', 'inventory_levels') }} r
  join {{ source('shopify_inventory', 'ingestion_runs') }} m
    on r.shop_key = m.shop_key
    and r.extraction_id = m.extraction_id
    and m.stream = 'inventory_levels'
    and m.status = 'published'
    and m.transport = 'shopify_graphql_pages'
  cross join unnest(json_query_array(m.files)) f
  where json_value(f, '$.role') = 'response_page'
    and json_value(f, '$.generation') = r.file_id
    and json_value(f, '$.sha256') = r.record_sha256
)
{% endmacro %}
