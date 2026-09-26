{{ config(tags=['crm']) }}
-- stg_klaviyo__events collapses observations to one row per dedup key
-- (event_id_basis, provider_event_id). This guards that collapse: the same
-- uuid must never survive under two different dedup keys (observed once with
-- its event_id and once without, or under two distinct event_ids) -- it would
-- duplicate a business event and inflate open/click counts. The raw merge on
-- event identity makes this near-impossible today; the test stays as the
-- regression guard for the collapse contract.
with uuid_dedup_keys as (
    select distinct
        shop_key,
        uuid,
        event_id_basis,
        nullif(trim(event_id), '') as event_id
    from {{ ref('stg_klaviyo__events') }}
    where uuid is not null
)
, uuid_groups as (
    select
        shop_key,
        uuid,
        count(*) as dedup_key_count
    from uuid_dedup_keys
    group by shop_key, uuid
)
select
    shop_key,
    uuid,
    dedup_key_count
from uuid_groups
where dedup_key_count > 1
