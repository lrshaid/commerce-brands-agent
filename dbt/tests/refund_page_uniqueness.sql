{{ config(tags=['refund_staging']) }}
-- page_key uniqueness for raw refund pages (previously enforced on stg_shopify__refund_pages).
with pages as (
    select * from {{ shopify_refund_pages() }}
)
select page_key, count(*) as cnt
from pages
group by page_key
having count(*) > 1
