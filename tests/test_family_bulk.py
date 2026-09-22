from contextlib import contextmanager
from datetime import datetime, timezone
import io
import json
from unittest.mock import Mock, patch

import pytest
from graphql import parse
from google.api_core.exceptions import PreconditionFailed

from agent.warehouse.family_bulk import FamilyBulkCapture, validate_bulk_rows, validate_family_publication
from agent.warehouse.raw_records import iter_raw_records
from agent.warehouse.shopify_bulk import bind_bulk_query, BulkError
from agent.warehouse.shopify_entities import _facts
from agent.warehouse.shopify_export import CompletedExport

NOW = datetime(2026, 9, 20, tzinfo=timezone.utc)
P = 'gid://shopify/Product/1'
V = 'gid://shopify/ProductVariant/1'
I = 'gid://shopify/InventoryItem/1'
L = 'gid://shopify/Location/1'


class Blob:
    def __init__(self, name, bucket):
        self.name, self.bucket = name, bucket
        self.body, self.generation, self.metadata, self.size = b'', None, {}, 0

    def upload_from_string(self, body, **kwargs):
        if self.generation is not None and kwargs.get('if_generation_match') == 0:
            raise PreconditionFailed('exists')
        self.body = body.encode() if isinstance(body, str) else body
        self.size = len(self.body)
        self.bucket.counter += 1
        self.generation = self.bucket.counter

    def upload_from_file(self, source, **kwargs):
        self.upload_from_string(source.read(), **kwargs)

    def download_as_bytes(self, **kwargs):
        assert kwargs.get('if_generation_match') == self.generation
        return self.body

    def download_to_file(self, target, **kwargs):
        target.write(self.download_as_bytes(**kwargs))


class Bucket:
    name = 'test-bucket'
    def __init__(self):
        self.objects, self.counter = {}, 0
    def blob(self, name):
        return self.objects.setdefault(name, Blob(name, self))
    def get_blob(self, name):
        blob = self.objects.get(name)
        return blob if blob and blob.generation is not None else None


def capture(family='catalog', **kwargs):
    return FamilyBulkCapture(bucket=kwargs.pop('bucket', Bucket()), domain='test.myshopify.com',
        api_version='2026-04', shop_gid='gid://shopify/Shop/1', extraction_id='test',
        family=family, search_filter=kwargs.pop('search_filter', 'updated_at:>=2026-09-13 updated_at:<2026-09-20'), **kwargs)


def records(cap, op, nodes):
    return list(iter_raw_records(io.BytesIO(b''.join(json.dumps(n).encode()+b'\n' for n in nodes)), cap.identity(op, '1', NOW)))


def test_binder_restricts_roots_and_binds_quotes():
    source = 'query($query: String) { products(query: $query) { edges { node { id } } } }'
    doc = parse(bind_bulk_query(source, 'title:"test"', root='products'))
    assert not doc.definitions[0].variable_definitions
    assert doc.definitions[0].selection_set.selections[0].arguments[0].value.value == 'title:"test"'
    with pytest.raises(BulkError):
        bind_bulk_query(source, 'x', root='customers')
    with pytest.raises(BulkError):
        bind_bulk_query('mutation { products { id } }', 'x', root='products')


def test_unordered_children_preserve_variant_owner_and_reject_orphans_duplicates_counts():
    cap = capture()
    rows = records(cap, 'products', [{'id': V, '__parentId': P, 'price': '1.25'}, {'id': P}])
    validate_bulk_rows('variants', rows, object_count=2, root_count=1)
    facts = list(_facts('variants', lambda: iter(rows), [{'generation': '1', 'role': 'bulk_jsonl'}], NOW))
    assert facts[0][1]['price'] == '1.25' and facts[0][2]['product_gid'] == P
    for bad, count, roots in [(rows[:1], 1, 0), (rows+rows, 4, 2), (rows, 3, 1)]:
        with pytest.raises(ValueError):
            validate_bulk_rows('variants', bad, object_count=count, root_count=roots)


def test_inventory_parent_and_available_quantity():
    cap = capture('inventory')
    child = {'id': 'gid://shopify/InventoryLevel/1', '__parentId': L,
             'item': {'id': I}, 'location': {'id': L}, 'quantities': [{'name':'available', 'quantity':7}]}
    rows = records(cap, 'inventory_levels', [child, {'id': L}])
    validate_bulk_rows('inventory_levels', rows, object_count=2, root_count=1)
    fact = next(_facts('inventory_levels', lambda:iter(rows), [{'generation':'1', 'role':'bulk_jsonl'}], NOW))
    assert fact[1]['quantity'] == 7 and fact[2]['location_gid'] == L
    child['location']['id'] = 'gid://shopify/Location/2'
    with pytest.raises(ValueError, match='location'):
        validate_bulk_rows('inventory_levels', records(cap, 'inventory_levels', [child, {'id':L}]), object_count=2, root_count=1)


def run_capture(cap, payloads):
    exports = {}
    def submit(**kw):
        op = kw['query_root']
        exports[op] = payloads[op]
        return op
    cap.client.submit_once.side_effect = submit
    def wait(client, op):
        nodes = exports[op]
        body = b''.join(json.dumps(n).encode()+b'\n' for n in nodes)
        roots = sum('__parentId' not in n for n in nodes)
        return CompletedExport('gid://shopify/BulkOperation/1', len(nodes), roots, len(body), NOW, NOW, body)
    @contextmanager
    def download(export):
        yield io.BytesIO(export.url)
    with patch('agent.warehouse.family_bulk.wait_for_export', side_effect=wait), patch('agent.warehouse.family_bulk.download_export', download):
        return cap.collect()


def test_capture_replay_empty_stream_and_tamper_detection():
    cap = capture(client=Mock())
    seal = run_capture(cap, {'customers': [], 'products': [{'id':P}, {'id':V, '__parentId':P}]})
    assert cap.client.submit_once.call_count == 2
    replay = capture(bucket=cap.bucket)
    prepared = replay.prepare(NOW)
    assert prepared['customers']['raw_record_count'] == 0
    assert prepared['products']['files'][0]['uri'] == prepared['variants']['files'][0]['uri']
    assert len(list(prepared['variants']['records'])) == 2
    with pytest.raises(ValueError, match='different bulk capture'):
        capture(bucket=cap.bucket, search_filter='updated_at:>=2026-01-01').collect()
    ref = seal['exports']['products']
    cap.bucket.get_blob(ref['uri'].split('/',3)[-1]).body += b' '
    with pytest.raises(ValueError, match='checksum'):
        replay.collect()


def test_inventory_country_codes_preserved_and_replayed_without_api():
    client=Mock()
    client._request.return_value = {'inventoryItem': {'id': I, 'countryHarmonizedSystemCodes': {
        'edges': [{'node': {'countryCode':'AR','harmonizedSystemCode':'7113'}}],
        'pageInfo': {'hasNextPage':False,'endCursor':None}}}}
    cap = capture('inventory', client=client)
    run_capture(cap, {'inventoryItems':[{'id':I}], 'locations':[]})
    result = capture('inventory', bucket=cap.bucket).prepare(NOW)['inventory_items']
    rows = list(result['records'])
    fact = next(_facts('inventory_items', lambda:iter(rows), result['files'], NOW))
    assert fact[1]['countryHarmonizedSystemCodes']['edges'][0]['node']['countryCode'] == 'AR'
    assert 'countryHarmonizedSystemCodes' not in json.loads(rows[0]['record_text'])
    assert client._request.call_count == 1


def test_fulfillments_are_inline_and_not_capped_at_fifty():
    cap = capture('fulfillments')
    nodes = [{'id':'gid://shopify/Order/1','fulfillments':[{'id':f'gid://shopify/Fulfillment/{n}'} for n in range(51)]}]
    rows = records(cap, 'fulfillments', nodes)
    validate_bulk_rows('fulfillments', rows, object_count=1, root_count=1)
    assert len(list(_facts('fulfillments', lambda:iter(rows), [{'generation':'1','role':'bulk_jsonl'}],NOW))) == 51


def test_bulk_assets_keep_launcher_keys_verify_shop_and_publish_nothing():
    import importlib
    import os
    from tests.test_new_streams_pipeline import ENV, CONFIG
    from orchestration.shopify_orders import OrdersConfig
    config = OrdersConfig(**CONFIG)
    for family in ('catalog', 'fulfillments', 'inventory'):
        module = importlib.import_module('orchestration.shopify_' + family)
        with patch.dict(os.environ, ENV), patch.object(module, 'BulkClient') as client, \
                patch.object(module.storage, 'Client'), patch.object(module, 'FamilyBulkCapture') as cap:
            client.return_value.verify_shop.return_value = config.expected_shop_gid
            cap.return_value.collect.return_value = {'exports': {'example': {'object_count': 3}}}
            result = getattr(module, 'shopify_' + family).op.compute_fn.decorated_fn(Mock(), config)
            client.return_value.verify_shop.assert_called_once_with(config.expected_shop_gid)
            assert cap.call_args.kwargs['client'] is client.return_value
            assert cap.call_args.kwargs['family'] == family
            assert 'page_size' not in cap.call_args.kwargs
            assert result.metadata['warehouse_published'] is False
            client.return_value.verify_shop.side_effect = ValueError('Wrong shop')
            cap.reset_mock()
            with pytest.raises(ValueError, match='Wrong shop'):
                getattr(module, 'shopify_' + family).op.compute_fn.decorated_fn(Mock(), config)
            cap.assert_not_called()


def test_publication_rejects_modified_counts_and_missing_inventory_enrichment():
    cap = capture(client=Mock())
    run_capture(cap, {'customers': [], 'products': [{'id':P}, {'id':V, '__parentId':P}]})
    result = capture(bucket=cap.bucket).prepare(NOW)['products']
    rows = list(result['records'])
    manifest = dict(provider_object_count=2, root_object_count=1,
                    bulk_operation_gid=result['bulk_operation_id'], transport=result['transport'],
                    shop_key=cap.shop_gid, extraction_id=cap.extraction_id,
                    query_sha256=result['query_sha256'], request_sha256=result['request_sha256'],
                    actual_api_version=cap.api_version)
    validate_family_publication('products', rows, result['files'], manifest)
    manifest['provider_object_count'] = 1
    with pytest.raises(ValueError, match='manifest'):
        validate_family_publication('products', rows, result['files'], manifest)


def test_incomplete_supplemental_country_codes_fail_closed():
    from agent.warehouse.family_bulk import country_codes
    with pytest.raises(ValueError, match='Incomplete'):
        country_codes({'id': I, 'pages':[{'inventoryItem': {'id':I,
            'countryHarmonizedSystemCodes': {'edges':[], 'pageInfo':{'hasNextPage':True,'endCursor':'x'}}}}]},I)
