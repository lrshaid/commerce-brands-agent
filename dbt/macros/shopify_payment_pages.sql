{#
  Parse raw payments HTTP pages into page-level helpers.  These are transport
  metadata, not business models; each payments stream lives in its own raw
  table, so every macro binds one stream and one source table.
#}
{% macro shopify_tender_transaction_pages() %}
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
  from {{ source('shopify_payments', 'tender_transactions') }} r
  join {{ source('shopify_payments', 'ingestion_runs') }} m
    on r.shop_key = m.shop_key
    and r.extraction_id = m.extraction_id
    and m.stream = 'tender_transactions'
    and m.status = 'published'
    and m.transport = 'shopify_graphql_pages'
  cross join unnest(json_query_array(m.files)) f
  where json_value(f, '$.role') = 'response_page'
    and json_value(f, '$.generation') = r.file_id
    and json_value(f, '$.sha256') = r.record_sha256
)
{% endmacro %}
{% macro shopify_balance_transaction_pages() %}
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
  from {{ source('shopify_payments', 'balance_transactions') }} r
  join {{ source('shopify_payments', 'ingestion_runs') }} m
    on r.shop_key = m.shop_key
    and r.extraction_id = m.extraction_id
    and m.stream = 'balance_transactions'
    and m.status = 'published'
    and m.transport = 'shopify_graphql_pages'
  cross join unnest(json_query_array(m.files)) f
  where json_value(f, '$.role') = 'response_page'
    and json_value(f, '$.generation') = r.file_id
    and json_value(f, '$.sha256') = r.record_sha256
)
{% endmacro %}
{% macro shopify_dispute_pages() %}
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
  from {{ source('shopify_payments', 'disputes') }} r
  join {{ source('shopify_payments', 'ingestion_runs') }} m
    on r.shop_key = m.shop_key
    and r.extraction_id = m.extraction_id
    and m.stream = 'disputes'
    and m.status = 'published'
    and m.transport = 'shopify_graphql_pages'
  cross join unnest(json_query_array(m.files)) f
  where json_value(f, '$.role') = 'response_page'
    and json_value(f, '$.generation') = r.file_id
    and json_value(f, '$.sha256') = r.record_sha256
)
{% endmacro %}
