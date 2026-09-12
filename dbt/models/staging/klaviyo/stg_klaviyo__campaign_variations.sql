{{ config(tags=['klaviyo_staging']) }}
-- One row per campaign variation projected from the included[] resources of
-- the messages chain pages (the only chain carrying variations).  Channel-
-- specific content fields coalesce to NULL outside their channel; a message
-- targets exactly one channel.
with pages as (
    select
        to_hex(sha256(to_json_string(struct(r.shop_key, r.extraction_id, r.file_id, r.record_index)))) as page_key,
        r.shop_key, r.extraction_id, r.file_id, r.record_index,
        r.ingested_at, m.published_at, r.payload
    from {{ source('klaviyo_api', 'campaigns') }} r
    join {{ source('klaviyo_api', 'ingestion_runs') }} m
      on r.shop_key = m.shop_key and r.extraction_id = m.extraction_id
     and m.stream = 'campaigns' and m.status = 'published'
     and m.transport = 'klaviyo_jsonapi_pages'
    cross join unnest(json_query_array(m.files)) f
    where json_value(f, '$.role') = 'response_page'
      and json_value(f, '$.operation') = 'messages_list'
      and json_value(f, '$.generation') = r.file_id
      and json_value(f, '$.sha256') = r.record_sha256
)
select
    to_hex(sha256(to_json_string(struct(p.shop_key, p.extraction_id, p.file_id, p.record_index, included_offset)))) as variation_key,
    p.shop_key, p.extraction_id,
    json_value(i, '$.id') as variation_id,
    json_value(i, '$.relationships.campaign-message.data.id') as message_id,
    json_value(i, '$.attributes.definition.name') as variation_name,
    json_value(i, '$.attributes.definition.details.channel') as channel,
    json_value(i, '$.attributes.definition.details.template_id') as template_id,
    json_value(i, '$.attributes.definition.details.subject') as subject,
    json_value(i, '$.attributes.definition.details.preview_text') as preview_text,
    json_value(i, '$.attributes.definition.details.from_email') as from_email,
    json_value(i, '$.attributes.definition.details.from_label') as from_label,
    json_value(i, '$.attributes.definition.details.reply_to_email') as reply_to_email,
    json_value(i, '$.attributes.definition.details.body') as body,
    cast(json_value(i, '$.attributes.created') as timestamp) as created_at,
    cast(json_value(i, '$.attributes.updated') as timestamp) as updated_at,
    to_hex(sha256(to_json_string(struct(p.page_key, included_offset)))) as page_key,
    p.ingested_at, p.published_at
from pages p
cross join unnest(json_query_array(p.payload, '$.included')) i with offset included_offset
where json_value(i, '$.type') = 'campaign-variation'
