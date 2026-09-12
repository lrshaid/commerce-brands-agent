{{ config(tags=['klaviyo_staging']) }}
-- One row per Klaviyo campaign extracted from the exact captured HTTP response
-- pages of the archived-filtered snapshot chain.  Child audiences, messages and
-- variations stay in their own staging models keyed by parent ids; scheduling
-- details stay unprojected until the live response shape is verified.
with pages as (
    select
        to_hex(sha256(to_json_string(struct(r.shop_key, r.extraction_id, r.file_id, r.record_index)))) as page_key,
        r.shop_key, r.extraction_id, r.file_id, r.record_index, r.record_sha256,
        r.ingested_at, m.published_at, r.payload
    from {{ source('klaviyo_api', 'campaigns') }} r
    join {{ source('klaviyo_api', 'ingestion_runs') }} m
      on r.shop_key = m.shop_key and r.extraction_id = m.extraction_id
     and m.stream = 'campaigns' and m.status = 'published'
     and m.transport = 'klaviyo_jsonapi_pages'
    cross join unnest(json_query_array(m.files)) f
    where json_value(f, '$.role') = 'response_page'
      and json_value(f, '$.operation') = 'campaigns_list'
      and json_value(f, '$.generation') = r.file_id
      and json_value(f, '$.sha256') = r.record_sha256
)
select
    to_hex(sha256(to_json_string(struct(p.shop_key, p.extraction_id, p.file_id, p.record_index, campaign_offset)))) as campaign_key,
    p.shop_key, p.extraction_id,
    json_value(c, '$.id') as campaign_id,
    json_value(c, '$.attributes.definition.name') as name,
    json_value(c, '$.attributes.definition.builder') as builder,
    cast(json_value(c, '$.attributes.definition.archived') as bool) as archived,
    json_value(c, '$.attributes.definition.send_settings.send_timezone') as send_timezone,
    json_value(c, '$.attributes.definition.send_settings.send_strategy') as send_strategy,
    cast(json_value(c, '$.attributes.definition.send_settings.send_passed_rltz_immediately') as bool) as send_passed_rltz_immediately,
    cast(json_value(c, '$.attributes.definition.send_settings.throttle_percentage') as int64) as throttle_percentage,
    cast(json_value(c, '$.attributes.definition.send_settings.exit_condition_enabled') as bool) as exit_condition_enabled,
    json_value(c, '$.attributes.definition.send_settings.exit_condition_conversion_metric_id') as exit_condition_conversion_metric_id,
    cast(json_value(c, '$.attributes.created_at') as timestamp) as created_at,
    cast(json_value(c, '$.attributes.updated_at') as timestamp) as updated_at,
    to_hex(sha256(to_json_string(struct(p.page_key, campaign_offset)))) as page_key,
    p.ingested_at, p.published_at
from pages p
cross join unnest(json_query_array(p.payload, '$.data')) c with offset campaign_offset
