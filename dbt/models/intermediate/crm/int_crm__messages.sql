{{ config(materialized='view', tags=['intermediate_view', 'crm']) }}
-- One current metadata row per shop/message. Never join audiences into facts.
with messages as (
    select shop_key, message_id, campaign_id, name as message_name
    from {{ ref('stg_klaviyo__campaign_messages') }}
    where message_id is not null
    qualify row_number() over (
        partition by shop_key, message_id
        order by updated_at desc nulls last, published_at desc, ingested_at desc, message_key desc
    ) = 1
), campaigns as (
    select shop_key, campaign_id, name as campaign_name
    from {{ ref('stg_klaviyo__campaigns') }}
    where campaign_id is not null
    qualify row_number() over (
        partition by shop_key, campaign_id
        order by updated_at desc nulls last, published_at desc, ingested_at desc, campaign_key desc
    ) = 1
)
select m.shop_key, m.message_id, m.campaign_id, m.message_name, c.campaign_name
from messages m
left join campaigns c on m.shop_key = c.shop_key and m.campaign_id = c.campaign_id
