"""Synthetic landing -> BQ atomic publication acceptance, isolated from Shopify."""
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import sys

from google.api_core.exceptions import BadRequest
from google.cloud import bigquery, storage
from google.oauth2.credentials import Credentials

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from agent.warehouse.raw_publication import contract_columns, initialize_tables, publish_records
from agent.warehouse.raw_records import ExtractionIdentity, iter_raw_records


def main():
    token = os.environ.get('GOOGLE_OAUTH_ACCESS_TOKEN')
    credentials = Credentials(token) if token else None
    bq = bigquery.Client(project='commerce-agents-dev', location='us-central1', credentials=credentials)
    gcs = storage.Client(project='commerce-agents-dev', credentials=credentials)
    name = 'raw/v1/acceptance/6e3c7ed8401597cd192acc0173d16ef54bff55f2d2a859bed4da9be9c5290f23.jsonl'
    generation = 1788493478468674
    blob = gcs.bucket('commerce-agents-dev-landing').blob(name, generation=generation)
    data = blob.download_as_bytes(if_generation_match=generation)
    assert hashlib.sha256(data).hexdigest() == '0792b07fe70c23898163b330f3986732fc45e4aa6e985de5645fa567ee62ff4a'
    now = datetime(2026, 9, 4, tzinfo=timezone.utc)
    identity = ExtractionIdentity('synthetic-acceptance-only', 'landing-probe-20260904-v1',
                                  str(generation), 'a'*64, 'b'*64, '2026-04', now)
    rows = list(iter_raw_records(io.BytesIO(data), identity))
    _, fields = contract_columns()
    manifest = dict.fromkeys(fields)
    manifest.update(shop_key=identity.shop_key, stream='acceptance', extraction_id=identity.extraction_id,
        contract_version=1, query_sha256=identity.query_sha256, request_sha256=identity.request_sha256,
        requested_api_version=identity.api_version, actual_api_version=identity.api_version,
        transport='synthetic_fixture', started_at=now, completed_at=now, published_at=now,
        status='published', raw_record_count=1, provider_object_count=1, root_object_count=1,
        files=[{'uri': f'gs://{blob.bucket.name}/{name}', 'generation': str(generation),
                'sha256': hashlib.sha256(data).hexdigest()}],
        dagster_job_name='operator_acceptance_not_dagster', dagster_retry_number=0,
        code_revision='raw-publication-probe-v1')
    dataset = 'commerce-agents-dev.platform_smoke'
    initialize_tables(bq, dataset, 'acceptance')
    for _ in range(2):
        print(json.dumps(publish_records(bq, dataset, 'acceptance', rows, manifest,
                                         transport_validated=True)))
    changed = list(iter_raw_records(io.BytesIO(b'{"id":"changed"}\n'), identity))
    try:
        publish_records(bq, dataset, 'acceptance', changed, manifest, transport_validated=True)
    except BadRequest as error:
        if 'Conflicting replay record' not in str(error):
            raise
    else:
        raise AssertionError('Expected conflicting replay to roll back')
    query = f'''SELECT
      (SELECT COUNT(*) FROM `{dataset}.acceptance` WHERE shop_key=@shop AND extraction_id=@extraction) raw_count,
      (SELECT COUNT(*) FROM `{dataset}.ingestion_runs` WHERE shop_key=@shop AND extraction_id=@extraction AND status='published') manifests,
      (SELECT ANY_VALUE(record_sha256) FROM `{dataset}.acceptance` WHERE shop_key=@shop AND extraction_id=@extraction) digest'''
    result = list(bq.query(query, job_config=bigquery.QueryJobConfig(maximum_bytes_billed=33554432,
        query_parameters=[bigquery.ScalarQueryParameter('shop', 'STRING', identity.shop_key),
                          bigquery.ScalarQueryParameter('extraction', 'STRING', identity.extraction_id)])).result(timeout=120))[0]
    assert result.raw_count == 1 and result.manifests == 1 and result.digest == rows[0]['record_sha256'], dict(result)
    # Inject failure after the raw INSERT, before manifest INSERT. This is a
    # test-only client wrapper; production SQL has no bypass/failure switch.
    class FailAfterRawClient:
        def __getattr__(self, name):
            return getattr(bq, name)

        def query(self, sql, **kwargs):
            marker = f'INSERT INTO `{dataset}.ingestion_runs`'
            assert marker in sql
            sql = sql.replace(marker, "ASSERT FALSE AS 'intentional after-raw failure';\n" + marker)
            return bq.query(sql, **kwargs)

    rollback_id = identity.extraction_id + '-rollback'
    rollback_rows = [dict(row, extraction_id=rollback_id) for row in rows]
    rollback_manifest = dict(manifest, extraction_id=rollback_id)
    try:
        publish_records(FailAfterRawClient(), dataset, 'acceptance', rollback_rows,
                        rollback_manifest, transport_validated=True)
    except BadRequest as error:
        if 'intentional after-raw failure' not in str(error):
            raise
    else:
        raise AssertionError('Expected injected transaction failure')
    after = list(bq.query(query, job_config=bigquery.QueryJobConfig(maximum_bytes_billed=33554432,
        query_parameters=[bigquery.ScalarQueryParameter('shop', 'STRING', identity.shop_key),
                          bigquery.ScalarQueryParameter('extraction', 'STRING', rollback_id)])).result(timeout=120))[0]
    assert after.raw_count == 0 and after.manifests == 0, dict(after)
    print('PASS: published once, replay without duplication, conflict rejected, after-raw failure rolled back raw and manifest')


if __name__ == '__main__':
    main()
