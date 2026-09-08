-- Every staged variant must belong to a product observed in the same
-- shop/extraction. This checks source lineage only; it does not assert that
-- an incremental snapshot contains every historical/deleted product.
select v.*
from {{ ref('stg_shopify__product_variants') }} v
left join {{ ref('stg_shopify__products') }} p
  on p.shop_key = v.shop_key
 and p.extraction_id = v.extraction_id
 and p.product_gid = v.product_gid
where p.product_gid is null
