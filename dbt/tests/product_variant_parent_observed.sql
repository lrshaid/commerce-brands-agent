-- Every staged variant must belong to a product observed for the same shop.
-- This checks source lineage only; it does not assert that the current-state
-- snapshot contains every historical/deleted product.
select v.*
from {{ ref('stg_shopify__variants') }} v
left join {{ ref('stg_shopify__products') }} p
  on p.shop_key = v.shop_key
 and p.product_gid = v.product_gid
where p.product_gid is null
