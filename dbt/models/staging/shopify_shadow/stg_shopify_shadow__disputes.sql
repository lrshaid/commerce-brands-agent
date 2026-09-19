{{ config(materialized='view', contract={'enforced': true}) }}
select
    shop_key,
    dispute_gid,
    legacy_resource_id,
    amount,
    currency_code,
    reason,
    network_reason_code,
    status,
    type,
    initiated_at,
    evidence_due_by,
    evidence_sent_on,
    finalized_on,
    order_gid,
    original_payload,
    source_extraction_id,
    source_updated_at,
    source_published_at,
    extracted_at
from {{ source('shopify_entities_shadow', 'disputes') }}
