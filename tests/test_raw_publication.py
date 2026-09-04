from datetime import datetime, timezone
import hashlib
import json
import unittest

from agent.warehouse.raw_publication import (
    _validate_refund_page_publication,
    contract_columns,
    publication_sql,
    publish_records,
)


SHOP = 'gid://shopify/Shop/3'
ORDER = 'gid://shopify/Order/1'
REFUND = 'gid://shopify/Refund/2'
NOW = '2026-09-04T00:00:00+00:00'


def _response(operation, owner=None):
    if operation == 'orders':
        data = {'orders': {'edges': [{'node': {'id': ORDER, 'refunds': [{'id': REFUND}]}}],
                           'pageInfo': {'hasNextPage': False, 'endCursor': None}}}
    else:
        data = {'node': {'id': owner, operation: {'edges': [{'node': {'id': 'gid://shopify/Child/4'}}],
                                                          'pageInfo': {'hasNextPage': False, 'endCursor': None}}}}
    return json.dumps({'data': data}, separators=(',', ':'))


def _refund_fixture():
    raw, manifest_fields = contract_columns()
    files, rows = [], []
    pages = [('101', 'orders', {'first': 50, 'after': None, 'query': 'updated_at:>=2025-01-01'}),
             ('102', 'transactions', {'first': 50, 'after': None, 'id': REFUND}),
             ('103', 'orderAdjustments', {'first': 50, 'after': None, 'id': REFUND})]
    for generation, operation, variables in pages:
        text = _response(operation, REFUND)
        sha256 = hashlib.sha256(text.encode()).hexdigest()
        files.append(dict(uri=f'gs://landing/pages/{generation}.json', generation=generation,
                          sha256=sha256, request_sha256='a' * 64, operation=operation,
                          variables=variables, captured_at=NOW, role='response_page'))
        rows.append(dict(shop_key=SHOP, extraction_id='run-1', file_id=generation,
                         record_index=1, query_sha256='c' * 64, request_sha256='d' * 64,
                         api_version='2026-04', ingested_at=NOW, record_sha256=sha256,
                         record_text=text, payload=text, object_gid=None, parent_gid=None))
    files.append(dict(uri='gs://landing/pages/complete.json', generation='104', sha256='b' * 64,
                      role='completion_seal'))
    manifest = dict.fromkeys(manifest_fields)
    manifest.update(dict(shop_key=SHOP, stream='order_refunds', extraction_id='run-1',
                         contract_version=1, query_sha256='c' * 64, request_sha256='d' * 64,
                         requested_api_version='2026-04', actual_api_version='2026-04',
                         transport='shopify_graphql_pages', window_start=datetime(2025, 1, 1, tzinfo=timezone.utc),
                         window_end=datetime(2026, 9, 4, tzinfo=timezone.utc),
                         started_at=datetime(2026, 9, 4, tzinfo=timezone.utc),
                         completed_at=datetime(2026, 9, 4, tzinfo=timezone.utc),
                         published_at=datetime(2026, 9, 4, tzinfo=timezone.utc), status='published',
                         raw_record_count=len(rows), provider_object_count=None, root_object_count=1,
                         files=files, error_code=None, dagster_job_name='test', dagster_run_id='run',
                         dagster_step_key='step', dagster_retry_number=0, dagster_partition_key=None,
                         cloud_run_execution_name=None, code_revision='test'))
    assert set(rows[0]) == set(raw)
    return rows, manifest


class _NoMutationClient:
    def __getattr__(self, name):
        raise AssertionError(f'BigQuery client mutated during preflight: {name}')


class _RecordingJob:
    job_id = 'test-job'

    def result(self, timeout=None):
        return None


class _RecordingClient:
    def __init__(self):
        self.calls = []

    def create_table(self, table):
        self.calls.append(('create_table', table.table_id))

    def load_table_from_file(self, data, table_ref, job_config):
        self.calls.append(('load_table_from_file', table_ref.table_id))
        return _RecordingJob()

    def query(self, sql, job_config):
        self.calls.append(('query', sql))
        return _RecordingJob()


class RawPublicationTests(unittest.TestCase):
    def test_sql_has_guard_and_atomic_commit(self):
        sql = publication_sql('commerce-agents-dev.platform_smoke', 'acceptance', '_load_' + 'a'*32)
        self.assertLess(sql.index('BEGIN TRANSACTION'), sql.index('UPDATE'))
        self.assertLess(sql.index('Conflicting replay record'), sql.index('INSERT INTO'))
        self.assertTrue(sql.strip().endswith('COMMIT TRANSACTION;'))
        self.assertNotIn('DELETE FROM', sql)
        self.assertIn('PARSE_JSON(payload)', sql)

    def test_invalid_identifiers_and_blocked_exchange_rejected(self):
        for dataset, stream, stage in [('x`;DROP', 'orders', '_load_'+'a'*32),
                                       ('commerce-agents-dev.raw_shopify', 'exchanges', '_load_'+'a'*32),
                                       ('commerce-agents-dev.raw_shopify', 'orders', 'bad')]:
            with self.assertRaises(ValueError):
                publication_sql(dataset, stream, stage)

    def test_no_transport_validation_no_publication(self):
        with self.assertRaises(ValueError):
            publish_records(None, 'commerce-agents-dev.platform_smoke', 'acceptance', [], {})

    def test_refund_page_preflight_accepts_reordered_exact_replay_rows(self):
        rows, manifest = _refund_fixture()
        _validate_refund_page_publication(rows, manifest['files'])
        # BigQuery's transaction matches by the physical key, so retry order is irrelevant.
        _validate_refund_page_publication(list(reversed(rows)), manifest['files'])
        client = _RecordingClient()
        publish_records(client, 'commerce-agents-dev.raw_shopify', 'order_refunds', rows,
                        manifest, transport_validated=True)
        publish_records(client, 'commerce-agents-dev.raw_shopify', 'order_refunds',
                        list(reversed(rows)), manifest, transport_validated=True)
        self.assertEqual([call[0] for call in client.calls],
                         ['create_table', 'load_table_from_file', 'query',
                          'create_table', 'load_table_from_file', 'query'])

    def test_refund_page_preflight_rejects_duplicate_generation_and_omitted_page(self):
        rows, manifest = _refund_fixture()
        duplicate = [rows[0], rows[0], rows[2]]
        with self.assertRaisesRegex(ValueError, 'one-to-one|unique'):
            publish_records(_NoMutationClient(), 'commerce-agents-dev.raw_shopify',
                            'order_refunds', duplicate, manifest, transport_validated=True)

    def test_refund_page_preflight_rejects_missing_page_metadata_without_bq(self):
        rows, manifest = _refund_fixture()
        manifest['files'][1].pop('request_sha256')
        with self.assertRaisesRegex(ValueError, 'metadata'):
            publish_records(_NoMutationClient(), 'commerce-agents-dev.raw_shopify',
                            'order_refunds', rows, manifest, transport_validated=True)

    def test_refund_page_preflight_rejects_owner_mismatch_without_bq(self):
        rows, manifest = _refund_fixture()
        manifest['files'][1]['variables']['id'] = 'gid://shopify/Refund/999'
        with self.assertRaisesRegex(ValueError, 'owner'):
            publish_records(_NoMutationClient(), 'commerce-agents-dev.raw_shopify',
                            'order_refunds', rows, manifest, transport_validated=True)

    def test_refund_page_preflight_rejects_changed_hash_without_bq(self):
        rows, manifest = _refund_fixture()
        rows[1]['record_sha256'] = 'e' * 64
        with self.assertRaisesRegex(ValueError, 'checksum'):
            publish_records(_NoMutationClient(), 'commerce-agents-dev.raw_shopify',
                            'order_refunds', rows, manifest, transport_validated=True)

    def test_refund_page_preflight_rejects_completion_seal_as_raw_without_bq(self):
        rows, manifest = _refund_fixture()
        rows[0]['file_id'] = '104'
        with self.assertRaisesRegex(ValueError, 'one-to-one|response pages'):
            publish_records(_NoMutationClient(), 'commerce-agents-dev.raw_shopify',
                            'order_refunds', rows, manifest, transport_validated=True)


if __name__ == '__main__':
    unittest.main()
