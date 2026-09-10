{#
  Parse raw fulfillments HTTP pages into a unified page-level CTE.  Orders root
  pages carry no fulfillment payload; fulfillment observations come from the
  owner-scoped pages keyed by the owning order.
#}
{% macro shopify_fulfillment_pages() %}
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
  from {{ source('shopify_fulfillments', 'fulfillments') }} r
  join {{ source('shopify_fulfillments', 'ingestion_runs') }} m
    on r.shop_key = m.shop_key
    and r.extraction_id = m.extraction_id
    and m.stream = 'fulfillments'
    and m.status = 'published'
    and m.transport = 'shopify_graphql_pages'
  cross join unnest(json_query_array(m.files)) f
  where json_value(f, '$.role') = 'response_page'
    and json_value(f, '$.generation') = r.file_id
    and json_value(f, '$.sha256') = r.record_sha256
)
{% endmacro %}
