"""Read-only warehouse acceptance evidence for a published returns extraction."""
import argparse
import json
import os

from google.cloud import bigquery
from google.oauth2.credentials import Credentials


SQL = """
WITH manifests AS (
  SELECT * FROM `commerce-agents-dev.raw_shopify.ingestion_runs`
  WHERE shop_key=@shop AND extraction_id=@extraction AND stream='returns' AND status='published'
), raw AS (
  SELECT * FROM `commerce-agents-dev.raw_shopify.returns`
  WHERE shop_key=@shop AND extraction_id=@extraction
), files AS (
  SELECT JSON_VALUE(f,'$.role') role, JSON_VALUE(f,'$.generation') generation,
    JSON_VALUE(f,'$.sha256') sha256, JSON_VALUE(f,'$.operation') operation
  FROM manifests m, UNNEST(JSON_QUERY_ARRAY(m.files)) f
), page_files AS (SELECT * FROM files WHERE role='response_page'),
raw_stats AS (
  SELECT COUNT(*) raw_count,
    COUNT(DISTINCT CONCAT(file_id,':',CAST(record_index AS STRING))) unique_physical_keys,
    COUNTIF(record_index IS NULL OR record_index != 1) bad_record_index,
    COUNTIF(TO_HEX(SHA256(record_text)) != record_sha256) raw_hash_mismatches,
    COUNTIF(NOT EXISTS (SELECT 1 FROM page_files f WHERE f.generation=r.file_id AND f.sha256=r.record_sha256)) raw_pages_without_manifest
  FROM raw r
), raw_dupes AS (
  SELECT COUNTIF(n>1) duplicate_raw_files FROM (SELECT file_id, COUNT(*) n FROM raw GROUP BY file_id)
), file_stats AS (
  SELECT COUNTIF(role='response_page') response_page_files,
    COUNT(DISTINCT IF(role='response_page',generation,NULL)) distinct_response_page_generations,
    COUNTIF(role='completion_seal') completion_seals,
    COUNTIF(role='response_page' AND NOT EXISTS (SELECT 1 FROM raw r WHERE r.file_id=generation AND r.record_sha256=sha256)) manifest_pages_without_raw
  FROM files
), payload_stats AS (
  SELECT
    COALESCE(SUM(IF(p.operation='orders',ARRAY_LENGTH(JSON_QUERY_ARRAY(r.payload,'$.data.orders.edges')),0)),0) payload_order_count,
    COALESCE(SUM(IF(p.operation='returns',ARRAY_LENGTH(JSON_QUERY_ARRAY(r.payload,'$.data.node.returns.edges')),0)),0) payload_return_count,
    COALESCE(SUM(IF(p.operation='returnLineItems',ARRAY_LENGTH(JSON_QUERY_ARRAY(r.payload,'$.data.node.returnLineItems.edges')),0)),0) payload_line_count,
    COALESCE(SUM(IF(p.operation='refunds',ARRAY_LENGTH(JSON_QUERY_ARRAY(r.payload,'$.data.node.refunds.edges')),0)),0) payload_refund_count
  FROM raw r JOIN page_files p ON p.generation=r.file_id AND p.sha256=r.record_sha256
), staged AS (
  SELECT
    (SELECT COUNT(*) FROM `commerce-agents-dev.analytics.stg_shopify__return_pages` WHERE shop_key=@shop AND extraction_id=@extraction) stg_page_count,
    (SELECT COUNT(*) FROM `commerce-agents-dev.analytics.stg_shopify__returns` WHERE shop_key=@shop AND extraction_id=@extraction) stg_return_count,
    (SELECT COUNT(*) FROM `commerce-agents-dev.analytics.stg_shopify__return_line_items` WHERE shop_key=@shop AND extraction_id=@extraction) stg_line_count,
    (SELECT COUNT(*) FROM `commerce-agents-dev.analytics.stg_shopify__return_refunds` WHERE shop_key=@shop AND extraction_id=@extraction) stg_refund_count,
    (SELECT COUNTIF(order_gid IS NULL OR return_gid IS NULL) FROM `commerce-agents-dev.analytics.stg_shopify__return_line_items` WHERE shop_key=@shop AND extraction_id=@extraction) null_line_parents,
    (SELECT COUNTIF(order_gid IS NULL OR return_gid IS NULL) FROM `commerce-agents-dev.analytics.stg_shopify__return_refunds` WHERE shop_key=@shop AND extraction_id=@extraction) null_refund_parents,
    (SELECT COUNT(*) FROM `commerce-agents-dev.analytics.stg_shopify__return_line_items` l LEFT JOIN `commerce-agents-dev.analytics.stg_shopify__returns` r USING(shop_key,extraction_id,return_gid) WHERE l.shop_key=@shop AND l.extraction_id=@extraction AND r.return_gid IS NULL) orphan_line_parents,
    (SELECT COUNT(*) FROM `commerce-agents-dev.analytics.stg_shopify__return_refunds` l LEFT JOIN `commerce-agents-dev.analytics.stg_shopify__returns` r USING(shop_key,extraction_id,return_gid) WHERE l.shop_key=@shop AND l.extraction_id=@extraction AND r.return_gid IS NULL) orphan_refund_parents
), manifest_stats AS (
  SELECT COUNT(*) manifest_count, ANY_VALUE(status) status, ANY_VALUE(transport) transport,
    ANY_VALUE(raw_record_count) manifest_raw_count, ANY_VALUE(root_object_count) manifest_root_count,
    ANY_VALUE(dagster_run_id) dagster_run_id, ANY_VALUE(cloud_run_execution_name) cloud_run_execution_name,
    ANY_VALUE(code_revision) code_revision
  FROM manifests
)
SELECT * FROM manifest_stats CROSS JOIN raw_stats CROSS JOIN raw_dupes CROSS JOIN file_stats
  CROSS JOIN payload_stats CROSS JOIN staged
"""


def validate_result(row):
    required = {
        'manifest_count': 1, 'status': 'published', 'transport': 'shopify_graphql_pages',
        'completion_seals': 1, 'bad_record_index': 0, 'raw_hash_mismatches': 0,
        'duplicate_raw_files': 0, 'raw_pages_without_manifest': 0,
        'manifest_pages_without_raw': 0, 'null_line_parents': 0,
        'null_refund_parents': 0, 'orphan_line_parents': 0, 'orphan_refund_parents': 0,
    }
    for key, expected in required.items():
        if row.get(key) != expected:
            raise RuntimeError(f'Return publication check failed: {key}={row.get(key)!r}, expected {expected!r}')
    for keys in (('raw_count', 'unique_physical_keys', 'stg_page_count', 'manifest_raw_count'),
                 ('payload_order_count', 'manifest_root_count'),
                 ('payload_return_count', 'stg_return_count'),
                 ('payload_line_count', 'stg_line_count'),
                 ('payload_refund_count', 'stg_refund_count')):
        if len({row.get(key) for key in keys}) != 1:
            raise RuntimeError(f'Return count reconciliation failed: {keys!r}')
    if row.get('response_page_files') != row.get('distinct_response_page_generations'):
        raise RuntimeError('Duplicate response-page manifest generations found')
    return row


def main():
    parser = argparse.ArgumentParser(description='Read-only returns warehouse verifier; requires GOOGLE_OAUTH_ACCESS_TOKEN.')
    parser.add_argument('--extraction-id', required=True)
    parser.add_argument('--shop-gid', required=True)
    args = parser.parse_args()
    if 'GOOGLE_OAUTH_ACCESS_TOKEN' not in os.environ or not os.environ['GOOGLE_OAUTH_ACCESS_TOKEN'].strip():
        raise RuntimeError('GOOGLE_OAUTH_ACCESS_TOKEN must be a non-empty explicit access token; refusing ADC fallback')
    client = bigquery.Client(project='commerce-agents-dev', location='us-central1',
                             credentials=Credentials(token=os.environ['GOOGLE_OAUTH_ACCESS_TOKEN'].strip()))
    job = client.query(SQL, job_config=bigquery.QueryJobConfig(maximum_bytes_billed=128 * 1024 * 1024,
        query_parameters=[bigquery.ScalarQueryParameter('extraction','STRING',args.extraction_id),
                          bigquery.ScalarQueryParameter('shop','STRING',args.shop_gid)]))
    rows = [dict(row) for row in job.result(timeout=120)]
    if len(rows) != 1:
        raise RuntimeError('Expected exactly one aggregate verification row')
    validate_result(rows[0])
    print(json.dumps({'verified': True, 'verification_job_id': job.job_id,
                      'extraction_id': args.extraction_id, 'shop_gid': args.shop_gid,
                      **rows[0]}, default=str, indent=2))


if __name__ == '__main__':
    main()
