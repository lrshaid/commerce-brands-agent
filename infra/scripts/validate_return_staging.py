"""Read-only BigQuery probe for the returns staging models."""
import hashlib
import json
import os
from pathlib import Path

from google.cloud import bigquery
from google.oauth2.credentials import Credentials
from jinja2 import Environment, StrictUndefined

ROOT = Path(__file__).resolve().parents[2]


def _body(data):
    return json.dumps({'data': data}, separators=(',', ':'))


def build_probe():
    order = 'gid://shopify/Order/1'
    ret1, ret2 = 'gid://shopify/Return/10', 'gid://shopify/Return/11'
    payloads = [
        ('orders', {}, {'orders': {'edges': [{'node': {'id': order, 'updatedAt': '2026-01-01T00:00:00Z'}}]}}),
        ('returns', {'id': order}, {'node': {'id': order, 'returns': {'edges': [{'node': {'id': ret1, 'name': '#10', 'status': 'OPEN', 'totalQuantity': 2, 'closedAt': None, 'requestApprovedAt': None}}]}}}),
        ('returns', {'id': order}, {'node': {'id': order, 'returns': {'edges': [{'node': {'id': ret2, 'name': '#11', 'status': 'CLOSED', 'totalQuantity': 0, 'closedAt': '2026-01-02T00:00:00Z', 'requestApprovedAt': None}}]}}}),
        ('returnLineItems', {'id': ret1}, {'node': {'id': ret1, 'returnLineItems': {'edges': [{'node': {'id': 'gid://shopify/ReturnLineItem/20', 'quantity': 1}}]}}}),
        ('returnLineItems', {'id': ret1}, {'node': {'id': ret1, 'returnLineItems': {'edges': [{'node': {'id': 'gid://shopify/ReturnLineItem/21', 'quantity': 1}}]}}}),
        ('refunds', {'id': ret1}, {'node': {'id': ret1, 'refunds': {'edges': [{'node': {'id': 'gid://shopify/Refund/30'}}]}}}),
        ('refunds', {'id': ret1}, {'node': {'id': ret1, 'refunds': {'edges': [{'node': {'id': 'gid://shopify/Refund/31'}}]}}}),
    ]
    texts = [_body(data) for _, _, data in payloads]
    files = [{'role': 'response_page', 'generation': str(i + 1), 'sha256': hashlib.sha256(texts[i].encode()).hexdigest(), 'operation': op, 'variables': variables, 'captured_at': '2026-01-01T00:00:00Z'} for i, (op, variables, _) in enumerate(payloads)]
    raw = """raw_pages as (select 'shop' shop_key, 'returns-probe' extraction_id, cast(i + 1 as string) file_id, 1 record_index, 'query' query_sha256, 'request' request_sha256, '2026-04' api_version, timestamp('2026-01-01') ingested_at, to_hex(sha256(body)) record_sha256, body record_text, parse_json(body) payload, cast(null as string) object_gid, cast(null as string) parent_gid from unnest(@bodies) body with offset i)"""
    manifest = """manifests as (select 'shop' shop_key, 'returns-probe' extraction_id, 'returns' stream, 'published' status, 'shopify_graphql_pages' transport, timestamp('2026-01-01') published_at, 7 raw_record_count, parse_json(@files) files)"""
    env = Environment(undefined=StrictUndefined)
    env.globals.update(source=lambda _, table: 'raw_pages' if table == 'returns' else 'manifests', ref=lambda name: name, config=lambda **_: None)
    ctes = [raw, manifest]
    for suffix in ('return_pages', 'returns', 'return_line_items', 'return_refunds'):
        name = 'stg_shopify__' + suffix
        sql = env.from_string((ROOT / 'dbt/models/staging/returns' / (name + '.sql')).read_text()).render()
        ctes.append(name + ' as (' + sql + ')')
    sql = 'with ' + ',\n'.join(ctes) + """
select (select count(*) from stg_shopify__return_pages) page_count,
  (select count(*) from stg_shopify__returns) return_count,
  (select count(*) from stg_shopify__return_line_items) line_count,
  (select count(*) from stg_shopify__return_refunds) refund_link_count,
  (select count(*) from stg_shopify__returns where total_quantity = 0) empty_return_count,
  (select count(*) from stg_shopify__return_line_items where order_gid is null or return_gid is null) missing_line_parents,
  (select count(*) from stg_shopify__return_refunds where order_gid is null or return_gid is null) missing_refund_parents,
  (select count(*) from stg_shopify__return_pages p where p.operation in ('returnLineItems','refunds') and not exists (select 1 from stg_shopify__returns r where r.return_gid = p.owner_gid)) orphan_child_pages
"""
    return sql, texts, files


def validate_result(row):
    expected = {'page_count': 7, 'return_count': 2, 'line_count': 2, 'refund_link_count': 2, 'empty_return_count': 1, 'missing_line_parents': 0, 'missing_refund_parents': 0, 'orphan_child_pages': 0}
    for key, value in expected.items():
        if row.get(key) != value:
            raise RuntimeError(f'return staging probe failed: {key}={row.get(key)!r}, expected {value!r}')
    return row


if __name__ == '__main__':
    token = os.environ.get('GOOGLE_OAUTH_ACCESS_TOKEN')
    client = bigquery.Client(project='commerce-agents-dev', location='us-central1', credentials=Credentials(token) if token else None)
    sql, texts, files = build_probe()
    job = client.query(sql, job_config=bigquery.QueryJobConfig(maximum_bytes_billed=10 * 1024 * 1024, query_parameters=[bigquery.ArrayQueryParameter('bodies', 'STRING', texts), bigquery.ScalarQueryParameter('files', 'STRING', json.dumps(files))]))
    row = dict(next(iter(job.result(timeout=120))))
    validate_result(row)
    print(json.dumps({'verified': True, 'verification_job_id': job.job_id, **row}, default=str))
