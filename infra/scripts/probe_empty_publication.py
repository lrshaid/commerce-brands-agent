"""Verify a completed empty synthetic extraction publishes without deleting history."""
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import sys

from google.cloud import bigquery, storage
from google.oauth2.credentials import Credentials

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from agent.warehouse.raw_landing import land_jsonl
from agent.warehouse.raw_publication import contract_columns, publish_records
from agent.warehouse.raw_records import ExtractionIdentity


def main():
    token = os.environ.get('GOOGLE_OAUTH_ACCESS_TOKEN')
    credentials = Credentials(token) if token else None
    client = bigquery.Client(project='commerce-agents-dev', location='us-central1', credentials=credentials)
    bucket = storage.Client(project=client.project, credentials=credentials).bucket(client.project + '-landing')
    now = datetime(2026, 9, 4, tzinfo=timezone.utc)
    identity = ExtractionIdentity('synthetic-acceptance-only', 'empty-probe-20260904-v1',
                                  'pending', 'a'*64, 'b'*64, '2026-04', now)
    landed = land_jsonl(io.BytesIO(b''), bucket, identity, 'acceptance')
    _, fields = contract_columns()
    manifest = dict.fromkeys(fields)
    manifest.update(shop_key=identity.shop_key, stream='acceptance', extraction_id=identity.extraction_id,
        contract_version=1, query_sha256='a'*64, request_sha256='b'*64,
        requested_api_version='2026-04', actual_api_version='2026-04', transport='synthetic_fixture',
        started_at=now, completed_at=now, published_at=now, status='published', raw_record_count=0,
        provider_object_count=0, root_object_count=0,
        files=[{k: landed[k] for k in ('uri', 'generation', 'sha256')}],
        dagster_job_name='operator_empty_acceptance', dagster_retry_number=0, code_revision='empty-probe-v1')
    for _ in range(2):
        print(json.dumps(publish_records(client, client.project + '.platform_smoke', 'acceptance', [],
                                         manifest, transport_validated=True)))
    query = '''SELECT
      (SELECT COUNT(*) FROM `commerce-agents-dev.platform_smoke.acceptance`
       WHERE shop_key=@shop AND extraction_id=@empty) empty_rows,
      (SELECT COUNT(*) FROM `commerce-agents-dev.platform_smoke.ingestion_runs`
       WHERE shop_key=@shop AND extraction_id=@empty AND status='published' AND raw_record_count=0) empty_manifests,
      (SELECT COUNT(*) FROM `commerce-agents-dev.platform_smoke.acceptance`
       WHERE shop_key=@shop AND extraction_id='landing-probe-20260904-v1') historical_rows'''
    job = client.query(query, job_config=bigquery.QueryJobConfig(maximum_bytes_billed=33554432,
        query_parameters=[bigquery.ScalarQueryParameter('shop', 'STRING', identity.shop_key),
                          bigquery.ScalarQueryParameter('empty', 'STRING', identity.extraction_id)]))
    result = dict(next(iter(job.result(timeout=120))))
    assert result == {'empty_rows': 0, 'empty_manifests': 1, 'historical_rows': 1}, result
    print(json.dumps({'result': 'PASS', 'verification_job': job.job_id, **result}))


if __name__ == '__main__':
    main()
