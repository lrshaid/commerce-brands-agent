{{ config(materialized='view', tags=['intermediate_view', 'crm']) }}
-- Published observations become one business event per shop. Source unchanged.
-- Missing provider IDs retain observation identity and are flagged, never merged.
with normalized as (
    select
        shop_key,
        coalesce(nullif(trim(event_id), ''), nullif(trim(uuid), ''), event_key) as provider_event_id,
        case when nullif(trim(event_id), '') is not null then 'event_id'
             when nullif(trim(uuid), '') is not null then 'uuid'
             else 'observation' end as event_id_basis,
        event_key as observation_key,
        extraction_id,
        nullif(trim(profile_id), '') as profile_id,
        case when nullif(trim(email), '') is not null
             then to_hex(sha256(lower(trim(email)))) end as email_identity_id,
        datetime as event_ts,
        event_type,
        metric_id,
        unknown_metric_id,
        nullif(trim(message), '') as message_id,
        nullif(trim(campaign), '') as campaign_id,
        nullif(trim(flow_id), '') as flow_id,
        campaign_name,
        message_name,
        subject,
        variant,
        method,
        ingested_at,
        published_at
    from {{ ref('stg_klaviyo__events') }}
), ranked as (
    select *, row_number() over (
        partition by shop_key, event_id_basis, provider_event_id
        order by published_at desc, ingested_at desc, observation_key desc
    ) as observation_rank
    from normalized
)
select
    to_hex(sha256(to_json_string(struct(shop_key, event_id_basis, provider_event_id)))) as crm_event_key,
    * except (observation_rank),
    case when event_type in ('received-email', 'opened-email', 'clicked-email',
                             'bounced-email', 'unsubscribed-from-email-marketing',
                             'marked-email-as-spam', 'dropped-email') then 'email'
         when event_type in ('received-text-message', 'clicked-text-message',
                             'sent-text-message', 'failed-to-deliver-text-message',
                             'unsubscribed-from-text-messaging-marketing') then 'sms'
         else 'other' end as event_channel,
    -- These flags are not projected by our source. NULL means unavailable.
    cast(null as bool) as machine_open,
    cast(null as bool) as bot_click
from ranked
where observation_rank = 1
