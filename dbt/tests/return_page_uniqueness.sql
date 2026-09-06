{{ config(tags=['returns_staging']) }}
-- page_key uniqueness for raw return pages (previously enforced on stg_shopify__return_pages).
with pages as (
    select * from {{ shopify_return_pages() }}
)
select page_key, count(*) as cnt
from pages
group by page_key
having count(*) > 1
