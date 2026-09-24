{{ config(materialized='view', tags=['crm']) }}
-- Atomic CRM event, no raw email. Profile ID / email hashes stay internal.
with enriched as (
    select e.* except (email_identity_id, campaign_id, campaign_name, message_name),
        case when e.profile_id is null then e.email_identity_id
             else i.customer_identity_id end as customer_identity_id,
        case when e.profile_id is null and e.email_identity_id is not null then 'event_email_exact'
             when e.profile_id is null then 'unidentified'
             else i.identity_status end as identity_status,
        coalesce(e.campaign_id, m.campaign_id) as campaign_id,
        case when e.campaign_id is null or e.campaign_id = m.campaign_id
             then coalesce(e.campaign_name, m.campaign_name)
             else e.campaign_name end as campaign_name,
        coalesce(e.message_name, m.message_name) as message_name,
        e.campaign_id is not null and m.campaign_id is not null
            and e.campaign_id != m.campaign_id as campaign_mapping_conflict
    from {{ ref('int_crm__events') }} e
    left join {{ ref('int_crm__profile_identity') }} i
        on e.shop_key = i.shop_key and e.profile_id = i.profile_id
    left join {{ ref('int_crm__messages') }} m
        on e.shop_key = m.shop_key and e.message_id = m.message_id
)
select *,
    coalesce(concat('customer:', customer_identity_id), concat('profile:', profile_id),
             concat('event:', crm_event_key)) as crm_person_key,
    date(event_ts) as event_date,
    case when flow_id is not null then 'flow'
         when campaign_id is not null then 'campaign' else 'unclassified' end as message_category
from enriched
