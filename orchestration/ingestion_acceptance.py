"""Synthetic end-to-end ingestion acceptance under the remote worker identity."""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import io
import os

import dagster as dg
from google.cloud import bigquery, storage

from agent.warehouse.raw_landing import land_jsonl
from agent.warehouse.raw_publication import contract_columns, initialize_tables, publish_records
from agent.warehouse.raw_records import ExtractionIdentity, iter_raw_records


@dg.multi_asset(specs=[
    dg.AssetSpec(key=['platform_smoke', 'acceptance'], group_name='platform_smoke'),
    dg.AssetSpec(key=['platform_smoke', 'ingestion_runs'], group_name='platform_smoke'),
])
def ingestion_probe(context: dg.AssetExecutionContext):
    project = os.environ['GOOGLE_CLOUD_PROJECT']
    region = os.environ.get('GOOGLE_CLOUD_REGION', 'us-central1')
    now = datetime.now(timezone.utc)
    identity = ExtractionIdentity('synthetic-worker-only', context.run_id, 'pending',
                                  'a'*64, 'b'*64, '2026-04', now)
    value = b'{"id":"synthetic-worker-probe","amount":"0.00"}\n'
    gcs = storage.Client(project=project)
    bucket = gcs.bucket(project + '-landing')
    landed = land_jsonl(io.BytesIO(value), bucket, identity, 'acceptance')
    identity = replace(identity, file_id=landed['generation'])
    name = landed['uri'].removeprefix(f'gs://{bucket.name}/')
    blob = bucket.blob(name, generation=int(landed['generation']))
    downloaded = blob.download_as_bytes(if_generation_match=int(landed['generation']))
    if hashlib.sha256(downloaded).hexdigest() != landed['sha256']:
        raise ValueError('Pinned landing checksum mismatch')
    rows = list(iter_raw_records(io.BytesIO(downloaded), identity))
    _, fields = contract_columns()
    manifest = dict.fromkeys(fields)
    manifest.update(shop_key=identity.shop_key, stream='acceptance', extraction_id=context.run_id,
        contract_version=1, query_sha256=identity.query_sha256, request_sha256=identity.request_sha256,
        requested_api_version=identity.api_version, actual_api_version=identity.api_version,
        transport='synthetic_fixture', started_at=now, completed_at=datetime.now(timezone.utc),
        published_at=datetime.now(timezone.utc), status='published', raw_record_count=len(rows),
        provider_object_count=1, root_object_count=1,
        files=[{k: landed[k] for k in ('uri', 'generation', 'sha256')}],
        dagster_job_name=context.job_name, dagster_run_id=context.run_id,
        dagster_step_key=context.op_execution_context.get_step_execution_context().step.key,
        dagster_retry_number=context.retry_number,
        cloud_run_execution_name=os.environ.get('CLOUD_RUN_EXECUTION'),
        code_revision=os.environ.get('CODE_VERSION', 'unknown'))
    client = bigquery.Client(project=project, location=region)
    dataset = project + '.platform_smoke'
    initialize_tables(client, dataset, 'acceptance')
    result = publish_records(client, dataset, 'acceptance', rows, manifest, transport_validated=True)
    for key in ('acceptance', 'ingestion_runs'):
        yield dg.MaterializeResult(asset_key=['platform_smoke', key],
            metadata={'raw_records': len(rows), 'landing_uri': landed['uri'],
                      'publication_job_id': result['publication_job_id']})
