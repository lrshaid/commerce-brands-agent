{# Logical node observations across v1 HTTP pages and v2 batches/top-ups.
   Original raw payloads remain untouched. Return children are once per Return,
   even when multiple refunds reference the same Return. #}
{% macro shopify_refund_child_nodes(operation) %}
{% set is_return = operation in ['returnLineItems', 'exchangeLineItems'] %}
(
with pages as {{ shopify_refund_pages() }}, batch_owners as (
    select p.*, owner_offset,
        {% if is_return %}json_query(owner, '$.return'){% else %}owner{% endif %} as owner_payload
    from pages p
    cross join unnest(json_query_array(p.payload, '$.data.nodes')) owner with offset owner_offset
    where p.operation = 'refundBatch'
), owners as (
    select b.*
    from batch_owners b
    {% if is_return %}
    qualify row_number() over (
        partition by shop_key, extraction_id, json_value(owner_payload, '$.id')
        order by captured_at, page_key, owner_offset
    ) = 1
    {% endif %}
), batch_nodes as (
    select shop_key, extraction_id, page_key, captured_at, published_at,
        json_value(owner_payload, '$.id') as owner_gid,
        to_hex(sha256(to_json_string(struct(page_key, owner_offset, '{{ operation }}', node_offset)))) as observation_key,
        n as node_payload
    from owners
    cross join unnest(json_query_array(owner_payload, '$.{{ operation }}.nodes')) n with offset node_offset
), topup_nodes as (
    select shop_key, extraction_id, page_key, captured_at, published_at,
        json_value(payload, '$.data.{{ "return" if is_return else "refund" }}.id') as owner_gid,
        to_hex(sha256(to_json_string(struct(page_key, '{{ operation }}', node_offset)))) as observation_key,
        n as node_payload
    from pages
    cross join unnest(json_query_array(payload, '$.data.{{ "return" if is_return else "refund" }}.{{ operation }}.nodes')) n with offset node_offset
    where operation = '{{ operation }}'
), legacy_nodes as (
    select shop_key, extraction_id, page_key, captured_at, published_at,
        refund_gid as owner_gid,
        to_hex(sha256(to_json_string(struct(page_key, node_offset)))) as observation_key,
        json_query(n, '$.node') as node_payload
    from pages
    cross join unnest(json_query_array(payload, '$.data.node.{{ operation }}.edges')) n with offset node_offset
    where operation = '{{ operation }}'
)
select * from batch_nodes
union all select * from topup_nodes
union all select * from legacy_nodes
)
{% endmacro %}
