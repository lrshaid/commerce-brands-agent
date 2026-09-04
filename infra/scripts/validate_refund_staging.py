"""Execute real staging SQL against synthetic CTEs; no tables are written."""
import hashlib
import json
import os
from pathlib import Path

from google.cloud import bigquery
from google.oauth2.credentials import Credentials
from jinja2 import Environment, StrictUndefined

ROOT = Path(__file__).resolve().parents[2]


def build_probe():
    order, refund = "gid://shopify/Order/1", "gid://shopify/Refund/2"
    bodies = [{"data": {"orders": {"edges": [{"node": {"id": order, "refunds": [{
        "id": refund, "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-01T00:00:00Z",
        "totalRefundedSet": {"shopMoney": {"amount": "12.34", "currencyCode": "USD"}}}]}}]}}}]
    operations = ["orders"]
    children = [
        ("refundLineItems", {"quantity": 1, "restockType": "RETURN", "subtotalSet": {"shopMoney": {"amount": "10.00"}}}),
        ("refundLineItems", {"quantity": 2, "restockType": "NO_RESTOCK", "subtotalSet": {"shopMoney": {"amount": "2.34"}}}),
        ("transactions", {"id": "gid://shopify/OrderTransaction/3", "kind": "REFUND", "status": "SUCCESS",
                          "amountSet": {"shopMoney": {"amount": "12.34"}}}),
        ("orderAdjustments", {"id": "gid://shopify/OrderAdjustment/4", "amountSet": {"shopMoney": {"amount": "-0.50"}}}),
    ]
    for operation, node in children:
        bodies.append({"data": {"node": {"id": refund, operation: {"edges": [{"node": node}]}}}})
        operations.append(operation)
    texts = [json.dumps(body) for body in bodies]
    files = [dict(role="response_page", generation=str(i+1), sha256=hashlib.sha256(body.encode()).hexdigest(),
                  operation=operations[i], variables={"id": refund} if i else {},
                  captured_at="2026-01-01T00:00:00Z") for i, body in enumerate(texts)]
    ctes = ["""raw_pages as (
        select 'shop' as shop_key, 'extraction' as extraction_id, cast(i+1 as string) as file_id,
               1 as record_index, parse_json(body) as payload, to_hex(sha256(body)) as record_sha256
        from unnest(@bodies) body with offset i
    )""", """manifests as (
        select 'shop' as shop_key, 'extraction' as extraction_id, 'order_refunds' as stream,
               'published' as status, 'shopify_graphql_pages' as transport,
               timestamp('2026-01-01') as published_at, parse_json(@files) as files
    )"""]
    env = Environment(undefined=StrictUndefined)
    env.globals.update(source=lambda _, table: "raw_pages" if table == "order_refunds" else "manifests",
                       ref=lambda name: name)
    names = ["refund_pages", "refunds", "refund_line_items", "refund_transactions", "refund_adjustments"]
    for suffix in names:
        name = "stg_shopify__" + suffix
        sql = env.from_string((ROOT / "dbt/models/staging/refunds" / (name + ".sql")).read_text()).render()
        ctes.append(name + " as (" + sql + ")")
    sql = "with " + ",\n".join(ctes) + """
    select (select count(*) from stg_shopify__refund_pages) as pages,
      (select count(*) from stg_shopify__refunds) as refunds,
      (select count(*) from stg_shopify__refund_line_items) as lines,
      (select sum(quantity) from stg_shopify__refund_line_items) as quantity,
      (select sum(subtotal_amount) from stg_shopify__refund_line_items) as subtotal,
      (select sum(amount) from stg_shopify__refund_transactions) as transaction_amount,
      (select sum(amount) from stg_shopify__refund_adjustments) as adjustment_amount,
      (select countif(order_gid != 'gid://shopify/Order/1' or order_gid is null)
       from stg_shopify__refund_line_items) as invalid_parent_links
    """
    return sql, texts, files


if __name__ == "__main__":
    from decimal import Decimal
    sql, texts, files = build_probe()
    client = bigquery.Client(project="commerce-agents-dev", location="us-central1",
        credentials=Credentials(os.environ["GOOGLE_OAUTH_ACCESS_TOKEN"]))
    job = client.query(sql, job_config=bigquery.QueryJobConfig(maximum_bytes_billed=10485760,
        query_parameters=[bigquery.ArrayQueryParameter("bodies", "STRING", texts),
                          bigquery.ScalarQueryParameter("files", "STRING", json.dumps(files))]))
    print(json.dumps({"verification_job_id": job.job_id}), flush=True)
    row = dict(next(iter(job.result(timeout=120))))
    assert row == dict(pages=5, refunds=1, lines=2, quantity=3, subtotal=Decimal("12.34"),
                       transaction_amount=Decimal("12.34"), adjustment_amount=Decimal("-0.50"), invalid_parent_links=0), row
    print(json.dumps(dict(verified=True, **row), default=str))
