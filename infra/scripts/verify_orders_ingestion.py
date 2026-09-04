"""Read-only warehouse evidence, limited to counts and execution identifiers."""
import argparse
import json
import os

from google.cloud import bigquery
from google.oauth2.credentials import Credentials

SQL = """
with counts as (
  select count(*) as raw_records, countif(parent_gid is null) as raw_roots,
         count(distinct to_json_string(struct(file_id, record_index))) as unique_record_keys,
         countif(starts_with(object_gid, 'gid://shopify/LineItem/')) as line_item_records,
         countif(starts_with(object_gid, 'gid://shopify/ShippingLine/')) as shipping_line_records,
         countif(object_gid is null and parent_gid is not null) as anonymous_child_records,
         countif(parent_gid is null and json_type(json_query(payload, '$.lineItems'))
           in ('object', 'array')) as inline_line_item_collections,
         countif(parent_gid is null and json_type(json_query(payload, '$.discountApplications'))
           in ('object', 'array')) as inline_discount_collections
  from `commerce-agents-dev.raw_shopify.orders`
  where extraction_id = @extraction and shop_key = @shop
), staged as (
  select count(*) as staged_records
  from `commerce-agents-dev.analytics.stg_shopify__order_records`
  where extraction_id = @extraction and shop_key = @shop
), roots as (
  select count(*) as staged_roots, min(updated_at) as min_updated_at,
         max(updated_at) as max_updated_at
  from `commerce-agents-dev.analytics.stg_shopify__orders`
  where extraction_id = @extraction and shop_key = @shop
)
select m.status, m.raw_record_count, m.provider_object_count, m.root_object_count,
       m.bulk_operation_gid, m.dagster_run_id, m.cloud_run_execution_name, m.code_revision,
       counts.*, staged.*, roots.*
from `commerce-agents-dev.raw_shopify.ingestion_runs` m
cross join counts cross join staged cross join roots
where m.extraction_id = @extraction and m.shop_key = @shop and m.stream = 'orders'
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--extraction-id", required=True)
    parser.add_argument("--shop-gid", required=True)
    args = parser.parse_args()
    token = os.environ.get("GOOGLE_OAUTH_ACCESS_TOKEN")
    client = bigquery.Client(project="commerce-agents-dev", location="us-central1",
                            credentials=Credentials(token) if token else None)
    job = client.query(SQL, job_config=bigquery.QueryJobConfig(
        maximum_bytes_billed=128 * 1024 * 1024,
        query_parameters=[bigquery.ScalarQueryParameter("extraction", "STRING", args.extraction_id),
                          bigquery.ScalarQueryParameter("shop", "STRING", args.shop_gid)]))
    print(json.dumps({"verification_job_id": job.job_id}), flush=True)
    rows = [dict(row) for row in job.result(timeout=120)]
    print(json.dumps(rows, default=str, indent=2))
    if len(rows) != 1:
        raise RuntimeError("Expected exactly one published extraction manifest")
    row = rows[0]
    if (row["status"] != "published" or len({row[k] for k in (
            "raw_records", "unique_record_keys", "staged_records", "raw_record_count", "provider_object_count")}) != 1
            or len({row[k] for k in ("raw_roots", "staged_roots", "root_object_count")}) != 1):
        raise RuntimeError("Published/raw/staged count reconciliation failed")


if __name__ == "__main__":
    main()
