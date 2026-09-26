{{ config(tags=['klaviyo_staging']) }}
-- One row per provider event per shop, read from the contract-flattened
-- event-grain raw stream. The publication merges on (shop_key, event_gid), so
-- re-captures of overlapping windows never accumulate duplicate raw rows; the
-- exact HTTP response pages stay the audit surface in GCS and every flattened
-- column stays recoverable from original_payload. The collapse below is a
-- safety net: the dedup key mirrors the source identifiers (event_id when
-- present, uuid fallback, observation key only when neither exists) and the
-- newest capture wins; observation_count preserves how many observations the
-- winner absorbed. event_type comes only from the klaviyo_metric_map var; an
-- unknown metric_id keeps event_type NULL and sets unknown_metric_id, it is
-- never guessed. email is a staging projection from the captured profile and
-- must never be promoted into marts.
with source as (
    select r.*, m.published_at
    from {{ source('klaviyo_api', 'events') }} r
    join {{ source('klaviyo_api', 'ingestion_runs') }} m
      on r.shop_key = m.shop_key and r.source_extraction_id = m.extraction_id
     and m.stream = 'events' and m.status = 'published'
     and m.transport = 'klaviyo_jsonapi_pages'
),
classified as (
    select
        to_hex(sha256(to_json_string(struct(r.shop_key, r.event_gid)))) as event_key,
        r.shop_key,
        r.source_extraction_id as extraction_id,
        r.event_gid as event_id,
        r.uuid,
        {{ klaviyo_metric_event_type("r.metric_id") }} as event_type,
        r.metric_id,
        r.profile_gid as profile_id,
        r.email,
        r.event_datetime as datetime,
        r.event_timestamp as timestamp,
        {{ klaviyo_unknown_metric("r.metric_id") }} as unknown_metric_id,
        to_hex(sha256(to_json_string(struct(r.source_extraction_id, r.event_gid)))) as page_key,
        r.ingested_at,
        r.published_at,
        r.flow_id, r.message, r.subject, r.campaign, r.campaign_name,
        r.message_name, r.method, r.channel, r.variant,
        to_json_string(r.list_ids) as list_ids
    from source r
),
identified as (
    select
        *,
        case when nullif(trim(event_id), '') is not null then 'event_id'
             when nullif(trim(uuid), '') is not null then 'uuid'
             else 'observation' end as event_id_basis,
        coalesce(nullif(trim(event_id), ''), nullif(trim(uuid), ''), event_key) as provider_event_id
    from classified
),
ranked as (
    select
        *,
        count(*) over (partition by shop_key, event_id_basis, provider_event_id) as observation_count,
        row_number() over (
            partition by shop_key, event_id_basis, provider_event_id
            order by published_at desc, ingested_at desc, event_key desc
        ) as observation_rank
    from identified
)
select * except (observation_rank)
from ranked
where observation_rank = 1
