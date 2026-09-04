"""Read-only warehouse evidence for a published Shopify refund-page extraction.

The query deliberately returns only technical identifiers and aggregate counts.  It
does not read GCS, call Shopify, or mutate BigQuery.  ``validate_result`` is kept
pure so the reconciliation contract can be tested without cloud credentials.
"""
import argparse
import json
import os

from google.cloud import bigquery
from google.oauth2.credentials import Credentials


SQL = """
WITH manifests AS (
  SELECT *
  FROM `commerce-agents-dev.raw_shopify.ingestion_runs`
  WHERE shop_key = @shop AND extraction_id = @extraction
    AND stream = 'order_refunds' AND status = 'published'
), raw AS (
  SELECT *
  FROM `commerce-agents-dev.raw_shopify.order_refunds`
  WHERE shop_key = @shop AND extraction_id = @extraction
), files AS (
  SELECT m.extraction_id, f,
    JSON_VALUE(f, '$.role') AS role,
    JSON_VALUE(f, '$.generation') AS generation,
    JSON_VALUE(f, '$.sha256') AS sha256,
    JSON_VALUE(f, '$.operation') AS operation
  FROM manifests m, UNNEST(JSON_QUERY_ARRAY(m.files)) f
), page_files AS (
  SELECT * FROM files WHERE role = 'response_page'
), raw_stats AS (
  SELECT COUNT(*) AS raw_count,
    COUNT(DISTINCT CONCAT(file_id, ':', CAST(record_index AS STRING))) AS unique_physical_keys,
    COUNTIF(record_index != 1 OR record_index IS NULL) AS bad_record_index,
    COUNTIF(TO_HEX(SHA256(record_text)) != record_sha256) AS raw_hash_mismatches,
    COUNTIF(NOT EXISTS (SELECT 1 FROM page_files p
      WHERE p.generation = r.file_id AND p.sha256 = r.record_sha256)) AS raw_pages_without_manifest,
    COUNTIF(record_index = 1) AS one_index_rows
  FROM raw r
), raw_duplicate_files AS (
  SELECT COUNTIF(n > 1) AS duplicate_raw_files
  FROM (SELECT file_id, COUNT(*) n FROM raw GROUP BY file_id)
), file_stats AS (
  SELECT
    COUNTIF(role = 'response_page') AS response_page_files,
    COUNT(DISTINCT IF(role = 'response_page', generation, NULL)) AS distinct_response_page_generations,
    COUNTIF(role = 'completion_seal') AS completion_seals,
    COUNTIF(role = 'response_page' AND NOT EXISTS (
      SELECT 1 FROM raw r WHERE r.file_id = generation AND r.record_sha256 = sha256
    )) AS manifest_pages_without_raw
  FROM files
), payload_stats AS (
  SELECT
    COALESCE(SUM(IF(operation = 'orders', ARRAY_LENGTH(JSON_QUERY_ARRAY(payload, '$.data.orders.edges')), 0)), 0) AS payload_order_count,
    COALESCE(SUM(IF(operation = 'orders', (SELECT COALESCE(SUM(ARRAY_LENGTH(JSON_QUERY_ARRAY(o, '$.node.refunds'))), 0)
      FROM UNNEST(JSON_QUERY_ARRAY(payload, '$.data.orders.edges')) o), 0)), 0) AS payload_refund_count,
    COALESCE(SUM(IF(operation = 'refundLineItems', ARRAY_LENGTH(JSON_QUERY_ARRAY(payload, '$.data.node.refundLineItems.edges')), 0)), 0) AS payload_line_count,
    COALESCE(SUM(IF(operation = 'transactions', ARRAY_LENGTH(JSON_QUERY_ARRAY(payload, '$.data.node.transactions.edges')), 0)), 0) AS payload_transaction_count,
    COALESCE(SUM(IF(operation = 'orderAdjustments', ARRAY_LENGTH(JSON_QUERY_ARRAY(payload, '$.data.node.orderAdjustments.edges')), 0)), 0) AS payload_adjustment_count
  FROM raw
  JOIN page_files p ON p.generation = raw.file_id AND p.sha256 = raw.record_sha256
), staged AS (
  SELECT
    (SELECT COUNT(*) FROM `commerce-agents-dev.analytics.stg_shopify__refund_pages` WHERE shop_key=@shop AND extraction_id=@extraction) AS stg_page_count,
    (SELECT COUNT(*) FROM `commerce-agents-dev.analytics.stg_shopify__refunds` WHERE shop_key=@shop AND extraction_id=@extraction) AS stg_refund_count,
    (SELECT COUNT(*) FROM `commerce-agents-dev.analytics.stg_shopify__refund_line_items` WHERE shop_key=@shop AND extraction_id=@extraction) AS stg_line_count,
    (SELECT COUNT(*) FROM `commerce-agents-dev.analytics.stg_shopify__refund_transactions` WHERE shop_key=@shop AND extraction_id=@extraction) AS stg_transaction_count,
    (SELECT COUNT(*) FROM `commerce-agents-dev.analytics.stg_shopify__refund_adjustments` WHERE shop_key=@shop AND extraction_id=@extraction) AS stg_adjustment_count,
    (SELECT COUNTIF(order_gid IS NULL) FROM `commerce-agents-dev.analytics.stg_shopify__refund_line_items` WHERE shop_key=@shop AND extraction_id=@extraction) AS null_line_parent_gids,
    (SELECT COUNTIF(order_gid IS NULL) FROM `commerce-agents-dev.analytics.stg_shopify__refund_transactions` WHERE shop_key=@shop AND extraction_id=@extraction) AS null_transaction_parent_gids,
    (SELECT COUNTIF(order_gid IS NULL) FROM `commerce-agents-dev.analytics.stg_shopify__refund_adjustments` WHERE shop_key=@shop AND extraction_id=@extraction) AS null_adjustment_parent_gids
), manifest_stats AS (
  SELECT COUNT(*) AS manifest_count,
    ANY_VALUE(status) AS status, ANY_VALUE(raw_record_count) AS manifest_raw_count,
    ANY_VALUE(root_object_count) AS manifest_root_count,
    ANY_VALUE(dagster_run_id) AS dagster_run_id,
    ANY_VALUE(cloud_run_execution_name) AS cloud_run_execution_name,
    ANY_VALUE(code_revision) AS code_revision
  FROM manifests
)
SELECT * FROM manifest_stats CROSS JOIN raw_stats CROSS JOIN raw_duplicate_files
  CROSS JOIN file_stats CROSS JOIN payload_stats CROSS JOIN staged
"""


def _same(row, *keys):
    values = [row.get(key) for key in keys]
    return len(set(values)) == 1


def validate_result(row):
    """Raise ``RuntimeError`` unless the published extraction reconciles fully."""
    required = {
        'manifest_count': 1, 'status': 'published', 'completion_seals': 1,
        'bad_record_index': 0, 'raw_hash_mismatches': 0,
        'duplicate_raw_files': 0, 'raw_pages_without_manifest': 0,
        'manifest_pages_without_raw': 0, 'null_line_parent_gids': 0,
        'null_transaction_parent_gids': 0, 'null_adjustment_parent_gids': 0,
    }
    for key, expected in required.items():
        if row.get(key) != expected:
            raise RuntimeError(f'Refund publication check failed: {key}={row.get(key)!r}, expected {expected!r}')
    if not _same(row, 'raw_count', 'unique_physical_keys', 'stg_page_count', 'manifest_raw_count'):
        raise RuntimeError('Refund raw/page count reconciliation failed')
    if row.get('response_page_files') != row.get('distinct_response_page_generations'):
        raise RuntimeError('Duplicate response-page manifest generations found')
    if not _same(row, 'payload_order_count', 'manifest_root_count'):
        raise RuntimeError('Root order count does not match the published manifest')
    if row.get('payload_refund_count') != row.get('stg_refund_count'):
        raise RuntimeError('Payload refund count does not match staged refunds')
    for payload, staged in (
        ('payload_line_count', 'stg_line_count'),
        ('payload_transaction_count', 'stg_transaction_count'),
        ('payload_adjustment_count', 'stg_adjustment_count'),
    ):
        if row.get(payload) != row.get(staged):
            raise RuntimeError(f'{payload} does not match {staged}')
    return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--extraction-id', required=True)
    parser.add_argument('--shop-gid', required=True)
    args = parser.parse_args()
    token = os.environ.get('GOOGLE_OAUTH_ACCESS_TOKEN')
    client = bigquery.Client(project='commerce-agents-dev', location='us-central1',
                             credentials=Credentials(token) if token else None)
    job = client.query(SQL, job_config=bigquery.QueryJobConfig(
        maximum_bytes_billed=128 * 1024 * 1024,
        query_parameters=[
            bigquery.ScalarQueryParameter('extraction', 'STRING', args.extraction_id),
            bigquery.ScalarQueryParameter('shop', 'STRING', args.shop_gid),
        ]))
    rows = [dict(row) for row in job.result(timeout=120)]
    print(json.dumps({'verification_job_id': job.job_id}), flush=True)
    if len(rows) != 1:
        raise RuntimeError('Verification query did not return its single aggregate row')
    result = validate_result(rows[0])
    # Technical evidence only: no payloads, record_text, addresses, notes, or ids
    # from Shopify objects are emitted.
    print(json.dumps({'verified': True, 'verification_job_id': job.job_id,
                      'extraction_id': args.extraction_id, 'shop_gid': args.shop_gid,
                      **result}, default=str, indent=2))


if __name__ == '__main__':
    main()
