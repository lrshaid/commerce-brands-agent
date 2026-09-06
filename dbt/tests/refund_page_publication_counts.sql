{{ config(tags=['refund_staging']) }}
-- Verify that every published refund extraction has exactly raw_record_count pages.
-- Uses the shopify_refund_pages macro directly because stg_shopify__refund_pages
-- was removed as a dbt model (pages are transport metadata, not business entities).
with pages as (
    select * from {{ shopify_refund_pages() }}
)
select m.shop_key, m.extraction_id
from {{ source('shopify_refunds', 'ingestion_runs') }} m
left join pages p
    on m.shop_key = p.shop_key and m.extraction_id = p.extraction_id
where m.stream = 'order_refunds' and m.status = 'published'
group by m.shop_key, m.extraction_id, m.raw_record_count
having count(p.page_key) != m.raw_record_count
