{#
  Parse raw order_refunds HTTP pages into a unified page-level CTE.
  This replaces the deleted stg_shopify__refund_pages dbt model; it is not
  itself a business model, only a raw-page parsing helper used by the flat
  refund staging models and by typed models when needed.
#}
{% macro shopify_refund_pages() %}
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
    json_value(f, '$.role') as file_role,
    json_value(f, '$.variables.id') as refund_gid,
    json_value(f, '$.variables.after') as after_cursor,
    cast(json_value(f, '$.captured_at') as timestamp) as captured_at,
    m.published_at,
    r.payload
  from {{ source('shopify_refunds', 'order_refunds') }} r
  join {{ source('shopify_refunds', 'ingestion_runs') }} m
    on r.shop_key = m.shop_key
    and r.extraction_id = m.extraction_id
    and m.stream = 'order_refunds'
    and m.status = 'published'
    and m.transport in ('shopify_graphql_pages', 'shopify_bulk_and_graphql_pages_v2')
  cross join unnest(json_query_array(m.files)) f
  where json_value(f, '$.role') in ('response_page', 'bulk_headers')
    and json_value(f, '$.generation') = r.file_id
    and (json_value(f, '$.role') = 'bulk_headers' or json_value(f, '$.sha256') = r.record_sha256)
)
{% endmacro %}
