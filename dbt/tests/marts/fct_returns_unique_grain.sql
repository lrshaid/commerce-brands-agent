-- fct_returns: unique grain (shop_key, extraction_id, order_line_item_id)
select shop_key, extraction_id, order_line_item_id, count(*) as n
from {{ ref('fct_returns') }}
group by shop_key, extraction_id, order_line_item_id
having count(*) > 1
