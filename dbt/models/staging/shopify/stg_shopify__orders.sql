select
    observation_key, shop_key, extraction_id, file_id, record_index,
    object_gid as order_gid,
    json_value(payload, '$.name') as order_name,
    cast(json_value(payload, '$.createdAt') as timestamp) as created_at,
    cast(json_value(payload, '$.updatedAt') as timestamp) as updated_at,
    cast(json_value(payload, '$.processedAt') as timestamp) as processed_at,
    cast(json_value(payload, '$.cancelledAt') as timestamp) as cancelled_at,
    json_value(payload, '$.currencyCode') as currency_code,
    json_value(payload, '$.displayFinancialStatus') as financial_status,
    json_value(payload, '$.displayFulfillmentStatus') as fulfillment_status,
    json_value(payload, '$.customer.id') as customer_gid,
    json_value(payload, '$.email') as email,
    json_value(payload, '$.note') as note,
    json_value_array(payload, '$.tags') as tags,
    json_query(payload, '$.shippingAddress') as shipping_address,
    json_query(payload, '$.billingAddress') as billing_address,
    {% for field, alias in [('totalPriceSet', 'total_price'), ('subtotalPriceSet', 'subtotal_price'),
                           ('totalTaxSet', 'total_tax'), ('totalDiscountsSet', 'total_discounts'),
                           ('totalShippingPriceSet', 'total_shipping_price')] %}
    cast(json_value(payload, '$.{{ field }}.shopMoney.amount') as numeric) as {{ alias }}_shop_amount,
    json_value(payload, '$.{{ field }}.shopMoney.currencyCode') as {{ alias }}_shop_currency,
    {% endfor %}
    cast(json_value(payload, '$.totalPriceSet.presentmentMoney.amount') as numeric) as total_price_presentment_amount,
    json_value(payload, '$.totalPriceSet.presentmentMoney.currencyCode') as total_price_presentment_currency,
    extraction_started_at, extraction_completed_at, published_at,
    payload as original_payload
from {{ ref('stg_shopify__order_records') }}
where parent_gid is null and starts_with(object_gid, 'gid://shopify/Order/')
