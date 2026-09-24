{{ config(materialized='view', tags=['crm']) }}
-- Event-date activity, including orphan interactions and non-email events.
-- Distinct people are exact within this cell only; never sum as period uniques.
select shop_key, event_date, event_channel, event_type, campaign_id, flow_id, message_id,
    message_category, count(*) as event_count,
    count(distinct crm_person_key) as observed_person_count,
    countif(identity_status in ('ambiguous_email', 'no_email', 'unidentified')) as unresolved_identity_events,
    countif(unknown_metric_id or event_type is null) as unknown_type_events,
    countif(event_id_basis = 'observation') as missing_provider_id_events,
    countif(campaign_mapping_conflict) as campaign_mapping_conflicts
from {{ ref('fct_crm_event') }}
group by shop_key, event_date, event_channel, event_type, campaign_id, flow_id, message_id, message_category
