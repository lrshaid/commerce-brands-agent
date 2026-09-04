"""Atomic raw/manifest publication after transport-specific validation.

The caller must validate provider completion and collection coverage separately.
This module guarantees warehouse atomicity/replay checks, not API completeness.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import tempfile
import uuid

from google.cloud import bigquery
import yaml


_REFUND_OPERATIONS = {'orders', 'refundLineItems', 'transactions', 'orderAdjustments'}
_RETURN_OPERATIONS = {'orders', 'returns', 'returnLineItems', 'refunds'}
_SHA256 = re.compile(r'[0-9a-f]{64}')


def _aware_timestamp(value):
    if not isinstance(value, str):
        return False
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).utcoffset() is not None
    except (TypeError, ValueError):
        return False


def _validate_refund_page_publication(rows, files):
    """Validate the page-specific physical grain before any BigQuery write."""
    response_pages = {}
    completion_seals = []
    generations = set()
    for source in files:
        if not isinstance(source, dict):
            raise ValueError('Refund manifest file must be an object')
        generation = source.get('generation')
        sha256 = source.get('sha256')
        if (not isinstance(generation, str) or not isinstance(sha256, str)
                or not str(source.get('uri', '')).startswith('gs://')
                or not generation.isdigit() or not _SHA256.fullmatch(sha256)):
            raise ValueError('Refund manifest file must include GCS URI, generation and SHA256')
        if generation in generations:
            raise ValueError('Refund manifest generations must be unique')
        generations.add(generation)
        role = source.get('role')
        if role == 'completion_seal':
            completion_seals.append(source)
            continue
        if role != 'response_page':
            raise ValueError('Refund manifest has an unsupported file role')
        operation = source.get('operation')
        request_sha256 = source.get('request_sha256')
        variables = source.get('variables')
        if (operation not in _REFUND_OPERATIONS or not isinstance(request_sha256, str)
                or not _SHA256.fullmatch(request_sha256) or not isinstance(variables, dict)
                or not _aware_timestamp(source.get('captured_at'))):
            raise ValueError('Refund response page metadata is incomplete or invalid')
        if generation in response_pages:
            raise ValueError('Refund response page generations must be unique')
        response_pages[generation] = source
    if len(completion_seals) != 1 or not response_pages:
        raise ValueError('Refund manifest requires one completion seal and response pages')

    rows_by_generation = {}
    for row in rows:
        if not isinstance(row.get('file_id'), str) or row.get('record_index') != 1:
            raise ValueError('Refund response pages require record_index=1')
        generation = str(row.get('file_id', ''))
        if generation not in response_pages or generation in rows_by_generation:
            raise ValueError('Refund raw rows must map one-to-one to response pages')
        text = row.get('record_text')
        if not isinstance(text, str) or row.get('payload') != text:
            raise ValueError('Refund raw row must preserve its original JSON text')
        try:
            body = text.encode('utf-8')
        except UnicodeEncodeError:
            raise ValueError('Refund raw row must be valid UTF-8 text') from None
        digest = hashlib.sha256(body).hexdigest()
        if (not isinstance(row.get('record_sha256'), str) or row.get('record_sha256') != digest
                or response_pages[generation].get('sha256') != digest):
            raise ValueError('Refund raw row or response page checksum mismatch')
        try:
            payload = json.loads(text)
        except (TypeError, ValueError, UnicodeError, RecursionError):
            raise ValueError('Refund response page is not valid JSON') from None
        if not isinstance(payload, dict) or payload.get('errors') or not isinstance(payload.get('data'), dict):
            raise ValueError('Refund response page is incomplete or has GraphQL errors')
        source = response_pages[generation]
        operation = source['operation']
        if operation == 'orders':
            if not isinstance(payload['data'].get('orders'), dict):
                raise ValueError('Refund orders page is missing its connection')
        else:
            owner = source['variables'].get('id')
            node = payload['data'].get('node')
            if not isinstance(owner, str) or not isinstance(node, dict) or node.get('id') != owner:
                raise ValueError('Refund response page owner does not match its request')
            if not isinstance(node.get(operation), dict):
                raise ValueError('Refund response page is missing its requested connection')
        rows_by_generation[generation] = row
    if set(rows_by_generation) != set(response_pages):
        raise ValueError('Refund raw rows omit a response page')


def _validate_returns_page_publication(rows, files):
    """Validate exact returns response-page grain before any BigQuery write."""
    response_pages = {}
    completion_seals = []
    generations = set()
    for source in files:
        if not isinstance(source, dict):
            raise ValueError('Returns manifest file must be an object')
        generation = source.get('generation')
        sha256 = source.get('sha256')
        if (not isinstance(generation, str) or not isinstance(sha256, str)
                or not str(source.get('uri', '')).startswith('gs://')
                or not generation.isdigit() or not _SHA256.fullmatch(sha256)):
            raise ValueError('Returns manifest file must include GCS URI, generation and SHA256')
        if generation in generations:
            raise ValueError('Returns manifest generations must be unique')
        generations.add(generation)
        role = source.get('role')
        if role == 'completion_seal':
            completion_seals.append(source)
            continue
        if role != 'response_page':
            raise ValueError('Returns manifest has an unsupported file role')
        operation = source.get('operation')
        request_sha256 = source.get('request_sha256')
        variables = source.get('variables')
        expected_variables = {'first', 'after', 'query'} if operation == 'orders' else {'first', 'after', 'id'}
        owner = variables.get('id') if isinstance(variables, dict) else None
        owner_pattern = (r'gid://shopify/Order/[0-9]+' if operation == 'returns'
                         else r'gid://shopify/Return/[0-9]+')
        if (operation not in _RETURN_OPERATIONS or not isinstance(request_sha256, str)
                or not _SHA256.fullmatch(request_sha256) or not isinstance(variables, dict)
                or set(variables) != expected_variables
                or not isinstance(variables.get('first'), int) or isinstance(variables.get('first'), bool)
                or not 1 <= variables.get('first', 0) <= 100
                or variables.get('after') is not None and not isinstance(variables.get('after'), str)
                or operation == 'orders' and not isinstance(variables.get('query'), str)
                or not _aware_timestamp(source.get('captured_at'))):
            raise ValueError('Returns response page metadata is incomplete or invalid')
        if operation != 'orders' and (not isinstance(owner, str) or not re.fullmatch(owner_pattern, owner)):
            raise ValueError('Returns response page owner metadata is invalid')
        if generation in response_pages:
            raise ValueError('Returns response page generations must be unique')
        response_pages[generation] = source
    if len(completion_seals) != 1 or not response_pages:
        raise ValueError('Returns manifest requires one completion seal and response pages')

    rows_by_generation = {}
    for row in rows:
        if not isinstance(row.get('file_id'), str) or row.get('record_index') != 1:
            raise ValueError('Returns response pages require record_index=1')
        generation = str(row.get('file_id', ''))
        if generation not in response_pages or generation in rows_by_generation:
            raise ValueError('Returns raw rows must map one-to-one to response pages')
        text = row.get('record_text')
        if not isinstance(text, str) or row.get('payload') != text:
            raise ValueError('Returns raw row must preserve its original JSON text')
        try:
            body = text.encode('utf-8')
        except UnicodeEncodeError:
            raise ValueError('Returns raw row must be valid UTF-8 text') from None
        digest_value = hashlib.sha256(body).hexdigest()
        if (not isinstance(row.get('record_sha256'), str) or row.get('record_sha256') != digest_value
                or response_pages[generation].get('sha256') != digest_value):
            raise ValueError('Returns raw row or response page checksum mismatch')
        try:
            payload = json.loads(text)
        except (TypeError, ValueError, UnicodeError, RecursionError):
            raise ValueError('Returns response page is not valid JSON') from None
        if not isinstance(payload, dict) or payload.get('errors') or not isinstance(payload.get('data'), dict):
            raise ValueError('Returns response page is incomplete or has GraphQL errors')
        source = response_pages[generation]
        operation = source['operation']
        if operation == 'orders':
            if not isinstance(payload['data'].get('orders'), dict):
                raise ValueError('Returns orders page is missing its connection')
        else:
            owner = source['variables'].get('id')
            node = payload['data'].get('node')
            if not isinstance(owner, str) or not isinstance(node, dict) or node.get('id') != owner:
                raise ValueError('Returns response page owner does not match its request')
            if not isinstance(node.get(operation), dict):
                raise ValueError('Returns response page is missing its requested connection')
        rows_by_generation[generation] = row
    if set(rows_by_generation) != set(response_pages):
        raise ValueError('Returns raw rows omit a response page')

CONTRACT = Path(__file__).resolve().parents[2] / 'warehouse/contracts/shopify_raw_v1.yaml'


def contract_columns():
    contract = yaml.safe_load(CONTRACT.read_text())
    raw = {k: v['type'] for k, v in contract['record_envelope']['columns'].items()}
    return raw, contract['run_manifest']['columns']


def dataset_id(value):
    if not re.fullmatch(r'[a-z][a-z0-9-]{4,61}[a-z0-9]\.[A-Za-z_][A-Za-z0-9_]*', value):
        raise ValueError('Expected project.dataset identifier')
    return value


def publication_sql(dataset, stream, stage):
    dataset_id(dataset)
    if stream not in ('orders', 'order_refunds', 'returns', 'acceptance'):
        raise ValueError('Stream has no publication contract')
    if not re.fullmatch('_load_[0-9a-f]{32}', stage):
        raise ValueError('Invalid staging identifier')
    raw, manifest = contract_columns()
    raw_fields = ', '.join(raw)
    normalized = ', '.join(f'PARSE_JSON({k}) AS {k}' if t == 'JSON' else k for k, t in raw.items())
    compare = ' OR '.join(f't.{k} IS DISTINCT FROM s.{k}' for k in raw if k not in ('payload', 'ingested_at'))
    key_match = ' AND '.join(f't.{k} = s.{k}' for k in ('shop_key', 'extraction_id', 'file_id', 'record_index'))
    manifest_values = ', '.join(f'PARSE_JSON(@m_{k})' if t == 'JSON' else f'@m_{k}' for k, t in manifest.items())
    return f'''
BEGIN TRANSACTION;
-- A real write to the pre-existing singleton forces concurrent publishers to
-- conflict/abort instead of both inserting an absent key under snapshot isolation.
UPDATE `{dataset}._publication_guard` SET epoch = epoch + 1 WHERE TRUE;
ASSERT @@row_count = 1 AS 'Publication guard must contain exactly one row';
CREATE TEMP TABLE candidate AS SELECT {normalized} FROM `{dataset}.{stage}`;
ASSERT (SELECT COUNT(*) FROM candidate) = @m_raw_record_count AS 'Raw count mismatch';
ASSERT NOT EXISTS(SELECT 1 FROM candidate WHERE shop_key != @m_shop_key
  OR extraction_id != @m_extraction_id OR query_sha256 != @m_query_sha256
  OR request_sha256 != @m_request_sha256 OR api_version != @m_actual_api_version)
  AS 'Record/manifest identity mismatch';
ASSERT NOT EXISTS(SELECT 1 FROM candidate GROUP BY shop_key, extraction_id,
  file_id, record_index HAVING COUNT(*) > 1) AS 'Duplicate candidate key';
ASSERT NOT EXISTS(SELECT 1 FROM `{dataset}.{stream}` t JOIN candidate s ON {key_match}
  WHERE {compare}) AS 'Conflicting replay record';
ASSERT (SELECT COUNT(*) FROM `{dataset}.ingestion_runs` WHERE shop_key = @m_shop_key
  AND stream = @m_stream AND extraction_id = @m_extraction_id) <= 1 AS 'Duplicate manifest key';
ASSERT NOT EXISTS(SELECT 1 FROM `{dataset}.ingestion_runs`
  WHERE shop_key = @m_shop_key AND stream = @m_stream AND extraction_id = @m_extraction_id
    AND (status != 'published' OR query_sha256 IS DISTINCT FROM @m_query_sha256
      OR request_sha256 IS DISTINCT FROM @m_request_sha256
      OR actual_api_version IS DISTINCT FROM @m_actual_api_version
      OR raw_record_count IS DISTINCT FROM @m_raw_record_count
      OR TO_JSON_STRING(files) != TO_JSON_STRING(PARSE_JSON(@m_files))))
  AS 'Conflicting replay manifest';
INSERT INTO `{dataset}.{stream}` ({raw_fields})
SELECT {', '.join('s.' + k for k in raw)} FROM candidate s
WHERE NOT EXISTS(SELECT 1 FROM `{dataset}.{stream}` t WHERE {key_match});
ASSERT (SELECT COUNT(*) FROM `{dataset}.{stream}` WHERE shop_key = @m_shop_key
  AND extraction_id = @m_extraction_id) = @m_raw_record_count AS 'Extraction has unexpected rows';
INSERT INTO `{dataset}.ingestion_runs` ({', '.join(manifest)})
SELECT {manifest_values} FROM UNNEST([1]) WHERE NOT EXISTS(SELECT 1 FROM `{dataset}.ingestion_runs`
  WHERE shop_key = @m_shop_key AND stream = @m_stream AND extraction_id = @m_extraction_id);
COMMIT TRANSACTION;
'''


def initialize_tables(client, dataset, stream):
    dataset_id(dataset)
    # Validate stream through the same whitelist as the transaction.
    publication_sql(dataset, stream, '_load_' + '0'*32)
    raw, manifest = contract_columns()
    for name, fields, partition in ((stream, raw, 'ingested_at'),
                                     ('ingestion_runs', manifest, 'published_at')):
        table = bigquery.Table(f'{dataset}.{name}', schema=[
            bigquery.SchemaField(k, t, mode='REQUIRED' if name == stream and k not in ('object_gid', 'parent_gid') else 'NULLABLE')
            for k, t in fields.items()])
        table.time_partitioning = bigquery.TimePartitioning(field=partition)
        table.clustering_fields = ['shop_key', 'extraction_id']
        client.create_table(table, exists_ok=True)
        actual = client.get_table(table.reference)
        aliases = {'INTEGER': 'INT64'}
        if {f.name: aliases.get(f.field_type, f.field_type) for f in actual.schema} != fields:
            raise ValueError(f'Existing table schema does not match contract: {name}')
    client.query(f'CREATE TABLE IF NOT EXISTS `{dataset}._publication_guard` AS SELECT 0 AS epoch',
                 job_config=bigquery.QueryJobConfig(maximum_bytes_billed=10485760)).result(timeout=120)


def publish_records(client, dataset, stream, records, manifest, *, transport_validated=False):
    """Publish fully validated records. No automatic retries after uncertain results.

    Returns job IDs for authoritative inspection. Concurrent transaction conflicts
    require an orchestrator retry with the same logical extraction identity.
    """
    if transport_validated is not True:
        raise ValueError('Provider completion and scope validation are required')
    raw, fields = contract_columns()
    if set(manifest) != set(fields):
        raise ValueError('Manifest must explicitly supply every contract field')
    for key in ('shop_key', 'extraction_id', 'query_sha256', 'request_sha256',
                'actual_api_version', 'requested_api_version', 'started_at', 'completed_at'):
        if not manifest[key]:
            raise ValueError(f'Missing required manifest identity: {key}')
    if manifest['status'] != 'published' or manifest['stream'] != stream or manifest['contract_version'] != 1:
        raise ValueError('Invalid publication state or contract version')
    if not manifest['published_at'] or manifest['error_code'] is not None:
        raise ValueError('Publication requires timestamp and no provider error')
    files = manifest['files']
    if not isinstance(files, list) or not files:
        raise ValueError('Manifest must reference durable source files, including empty results')
    file_ids = set()
    for source in files:
        if (not isinstance(source, dict) or not str(source.get('uri', '')).startswith('gs://')
                or not str(source.get('generation', '')).isdigit()
                or not re.fullmatch('[0-9a-f]{64}', str(source.get('sha256', '')))):
            raise ValueError('Source file must include GCS URI, generation and SHA256')
        file_ids.add(str(source['generation']))
    if stream == 'order_refunds' and manifest['transport'] == 'shopify_graphql_pages':
        # Materialize and validate this small page-grain stream before creating a
        # staging table. Orders/Bulk keeps its existing streaming behavior.
        refund_rows = []
        for row in records:
            if not isinstance(row, dict) or set(row) != set(raw):
                raise ValueError('Raw row must match envelope columns')
            if row['file_id'] not in file_ids or row['payload'] != row['record_text']:
                raise ValueError('Raw row must preserve its referenced file and original JSON')
            refund_rows.append(row)
        _validate_refund_page_publication(refund_rows, files)
        records = refund_rows
    if stream == 'returns':
        if manifest['transport'] != 'shopify_graphql_pages':
            raise ValueError('Returns publication requires shopify_graphql_pages transport')
        return_rows = []
        for row in records:
            if not isinstance(row, dict) or set(row) != set(raw):
                raise ValueError('Raw row must match envelope columns')
            if row['file_id'] not in file_ids or row['payload'] != row['record_text']:
                raise ValueError('Raw row must preserve its referenced file and original JSON')
            return_rows.append(row)
        _validate_returns_page_publication(return_rows, files)
        records = return_rows
    stage = '_load_' + uuid.uuid4().hex
    sql = publication_sql(dataset, stream, stage)
    with tempfile.SpooledTemporaryFile(max_size=4*1024*1024, mode='r+b') as data:
        count = 0
        for row in records:
            if set(row) != set(raw):
                raise ValueError('Raw row must match envelope columns')
            if row['file_id'] not in file_ids or row['payload'] != row['record_text']:
                raise ValueError('Raw row must preserve its referenced file and original JSON')
            data.write((json.dumps(row, ensure_ascii=False) + '\n').encode())
            count += 1
        if count != manifest['raw_record_count']:
            raise ValueError('Parsed record count does not match validated manifest')
        table = bigquery.Table(f'{dataset}.{stage}', schema=[
            bigquery.SchemaField(k, 'STRING' if t == 'JSON' else t,
                                mode='NULLABLE' if k in ('object_gid', 'parent_gid') else 'REQUIRED')
            for k, t in raw.items()])
        table.expires = datetime.now(timezone.utc) + timedelta(hours=24)
        client.create_table(table)
        load = None
        if count:
            data.seek(0)
            load = client.load_table_from_file(data, table.reference,
                job_config=bigquery.LoadJobConfig(source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
                                                 schema=table.schema, write_disposition='WRITE_EMPTY'))
            # Print technical handles to inspect timeouts without relaunching blindly.
            print(json.dumps({'event': 'raw_load_submitted', 'job_id': load.job_id, 'stage': stage}), flush=True)
            load.result(timeout=300)
    params = [bigquery.ScalarQueryParameter('m_' + k, 'STRING' if t == 'JSON' else t,
               json.dumps(manifest[k]) if t == 'JSON' else manifest[k]) for k, t in fields.items()]
    job = client.query(sql, job_config=bigquery.QueryJobConfig(
        query_parameters=params, maximum_bytes_billed=1073741824,
        labels={'purpose': 'raw_publication'}))
    print(json.dumps({'event': 'raw_publication_submitted', 'job_id': job.job_id}), flush=True)
    job.result(timeout=300)
    return {'load_job_id': load.job_id if load else None, 'publication_job_id': job.job_id,
            'stage_table': f'{dataset}.{stage}', 'published': True}
