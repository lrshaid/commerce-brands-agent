{#
  Parse raw returns HTTP pages into a unified page-level CTE.
  This replaces the deleted stg_shopify__return_pages dbt model; it is not
  itself a business model, only a raw-page parsing helper used by the flat
  return staging models and by typed models when needed.

  The manifest files array is extracted ONCE into a CTE and hash-joined on
  (generation, sha256); the previous cross-join-then-filter form evaluated
  json_value per (raw row × manifest file) combination — quadratic over JSON —
  and exceeded the query timeout with the first real store capture (15,546
  pages, 2026-09-14).
#}
{% macro shopify_return_pages() %}
(
  with manifest_files as (
      select
          m.shop_key,
          m.extraction_id,
          json_value(f, '$.generation') as generation,
          json_value(f, '$.sha256') as sha256,
          f as file_json,
          m.published_at
      from {{ source('shopify_returns', 'ingestion_runs') }} m
      cross join unnest(json_query_array(m.files)) f
      where m.stream = 'returns'
          and m.status = 'published'
          and m.transport = 'shopify_graphql_pages'
          and json_value(f, '$.role') = 'response_page'
  )
  select
      to_hex(sha256(to_json_string(struct(r.shop_key, r.extraction_id, r.file_id, r.record_index)))) as page_key,
      r.shop_key,
      r.extraction_id,
      r.file_id,
      r.record_index,
      r.record_sha256,
      r.record_text,
      r.ingested_at,
      json_value(f.file_json, '$.operation') as operation,
      json_value(f.file_json, '$.variables.id') as owner_gid,
      json_value(f.file_json, '$.variables.after') as after_cursor,
      cast(json_value(f.file_json, '$.captured_at') as timestamp) as captured_at,
      f.published_at,
      r.payload
  from {{ source('shopify_returns', 'returns') }} r
  join manifest_files f
      on r.shop_key = f.shop_key
      and r.extraction_id = f.extraction_id
      and r.file_id = f.generation
      and r.record_sha256 = f.sha256
)
{% endmacro %}
