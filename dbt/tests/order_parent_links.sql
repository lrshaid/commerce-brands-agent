with children as (
    select shop_key, order_gid, 'order_line_items' as child_type
    from {{ ref('stg_shopify__order_line_items') }}
    union all
    select shop_key, order_gid, 'order_shipping_lines' as child_type
    from {{ ref('stg_shopify__order_shipping_lines') }}
)
select c.shop_key, c.order_gid, c.child_type
from children c
left join {{ ref('stg_shopify__orders') }} o
    on c.shop_key = o.shop_key
    and c.order_gid = o.order_gid
where o.order_gid is null
