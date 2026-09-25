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

from .klaviyo_events_contract import EventContract

_KLAVIYO_EVENT_CONTRACT = EventContract()


_REFUND_OPERATIONS = {'orders', 'refundLineItems', 'transactions', 'orderAdjustments'}
_RETURN_OPERATIONS = {'orders', 'returns', 'returnLineItems', 'refunds'}
_CATALOG_OPERATIONS = {'customers', 'products', 'variants'}
_PAYMENT_STREAM_OPERATIONS = {'tender_transactions': 'tenderTransactions',
                              'balance_transactions': 'balanceTransactions',
                              'disputes': 'disputes'}
_PAYMENT_QUERY_OPERATIONS = {'tenderTransactions', 'disputes'}
_FULFILLMENT_OPERATIONS = {'orders', 'fulfillments'}
_FULFILLMENT_ORDER_STREAM_OPERATIONS = {
    'fulfillment_orders': ('fulfillmentOrders',),
    'fulfillment_order_line_items': ('lineItems',),
}
_INVENTORY_STREAM_OPERATIONS = {'inventory_items': ('inventoryItems',),
                                'inventory_levels': ('locations', 'inventoryLevels')}
_INVENTORY_QUERY_OPERATIONS = {'inventoryItems'}
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


def _validate_catalog_page_publication(rows, files, stream=None):
    """Validate customers/products/variants page grain before any write."""
    response_pages, seals, generations = {}, [], set()
    for source in files:
        if not isinstance(source, dict):
            raise ValueError('Catalog manifest file must be an object')
        generation, sha256 = source.get('generation'), source.get('sha256')
        if (not isinstance(generation, str) or not generation.isdigit()
                or not isinstance(sha256, str) or not _SHA256.fullmatch(sha256)
                or not str(source.get('uri', '')).startswith('gs://')):
            raise ValueError('Catalog manifest file must include GCS URI, generation and SHA256')
        if generation in generations:
            raise ValueError('Catalog manifest generations must be unique')
        generations.add(generation)
        if source.get('role') == 'completion_seal':
            seals.append(source)
            continue
        if source.get('role') != 'response_page' or source.get('operation') not in _CATALOG_OPERATIONS:
            raise ValueError('Catalog manifest has an unsupported file role or operation')
        if stream in _CATALOG_OPERATIONS and source.get('operation') != stream:
            raise ValueError('Catalog stream and response operation do not match')
        variables = source.get('variables')
        operation = source['operation']
        expected = {'first', 'after', 'query'} if operation in {'customers', 'products'} else {'first', 'after', 'id'}
        if (not isinstance(variables, dict) or set(variables) != expected
                or not isinstance(variables.get('first'), int) or isinstance(variables.get('first'), bool)
                or not 1 <= variables['first'] <= 100
                or variables.get('after') is not None and not isinstance(variables.get('after'), str)
                or operation in {'customers', 'products'} and not isinstance(variables.get('query'), str)
                or operation == 'variants' and not re.fullmatch(r'gid://shopify/Product/[0-9]+', str(variables.get('id')))
                or not isinstance(source.get('request_sha256'), str)
                or not _SHA256.fullmatch(source['request_sha256'])
                or not _aware_timestamp(source.get('captured_at'))):
            raise ValueError('Catalog response-page metadata is incomplete or invalid')
        response_pages[generation] = source
    if len(seals) != 1 or (not response_pages and stream != 'variants'):
        raise ValueError('Catalog manifest requires one completion seal and response pages')
    if stream == 'variants' and not response_pages:
        counts = seals[0].get('catalog_counts')
        if (not isinstance(counts, dict) or counts.get('products') != 0
                or counts.get('variants') != 0):
            raise ValueError('Empty variants stream requires an extraction with zero products')

    rows_by_generation = {}
    for row in rows:
        if (not isinstance(row, dict) or set(row) != set(contract_columns()[0])
                or not isinstance(row.get('file_id'), str) or row.get('record_index') != 1):
            raise ValueError('Catalog response pages require envelope columns and record_index=1')
        generation = row['file_id']
        if generation not in response_pages or generation in rows_by_generation:
            raise ValueError('Catalog raw rows must map one-to-one to response pages')
        text = row.get('record_text')
        if not isinstance(text, str) or row.get('payload') != text:
            raise ValueError('Catalog raw row must preserve original JSON text')
        digest_value = hashlib.sha256(text.encode('utf-8')).hexdigest()
        if row.get('record_sha256') != digest_value or response_pages[generation].get('sha256') != digest_value:
            raise ValueError('Catalog raw row or response page checksum mismatch')
        try:
            payload = json.loads(text)
        except (TypeError, ValueError, UnicodeError, RecursionError):
            raise ValueError('Catalog response page is not valid JSON') from None
        if not isinstance(payload, dict) or payload.get('errors') or not isinstance(payload.get('data'), dict):
            raise ValueError('Catalog response page is incomplete or has GraphQL errors')
        source = response_pages[generation]
        operation, variables = source['operation'], source['variables']
        if operation in {'customers', 'products'}:
            connection = payload['data'].get(operation)
        else:
            node = payload['data'].get('node')
            if not isinstance(node, dict) or node.get('id') != variables.get('id'):
                raise ValueError('Catalog response page owner does not match its request')
            connection = node.get('variants')
        if (not isinstance(connection, dict) or not isinstance(connection.get('pageInfo'), dict)
                or type(connection['pageInfo'].get('hasNextPage')) is not bool
                or (connection['pageInfo'].get('endCursor') is not None
                    and not isinstance(connection['pageInfo'].get('endCursor'), str))
                or not isinstance(connection.get('nodes'), list)):
            raise ValueError('Catalog response page is missing its connection')
        if connection['pageInfo']['hasNextPage'] and not connection['pageInfo'].get('endCursor'):
            raise ValueError('Catalog response page has a nonadvancing cursor')
        rows_by_generation[generation] = row
    if set(rows_by_generation) != set(response_pages):
        raise ValueError('Catalog raw rows omit a response page')

def _checked_page_files(files, allowed_operations):
    """Common manifest-file envelope validation shared by the page transports."""
    response_pages, seals, generations = {}, [], set()
    for source in files:
        if not isinstance(source, dict):
            raise ValueError('Manifest file must be an object')
        generation, sha256 = source.get('generation'), source.get('sha256')
        if (not isinstance(generation, str) or not generation.isdigit()
                or not isinstance(sha256, str) or not _SHA256.fullmatch(sha256)
                or not str(source.get('uri', '')).startswith('gs://')):
            raise ValueError('Manifest file must include GCS URI, generation and SHA256')
        if generation in generations:
            raise ValueError('Manifest generations must be unique')
        generations.add(generation)
        if source.get('role') == 'completion_seal':
            seals.append(source)
            continue
        if source.get('role') != 'response_page' or source.get('operation') not in allowed_operations:
            raise ValueError('Manifest has an unsupported file role or operation')
        response_pages[generation] = source
    return response_pages, seals


def _checked_page_variables(source, expected, owner_pattern=None, query_operation=False):
    variables = source.get('variables')
    first = variables.get('first') if isinstance(variables, dict) else None
    if (not isinstance(variables, dict) or set(variables) != expected
            or ('first' in expected
                and (not isinstance(first, int) or isinstance(first, bool) or not 1 <= first <= 100))
            or ('after' in expected and variables.get('after') is not None
                and not isinstance(variables.get('after'), str))
            or query_operation and not isinstance(variables.get('query'), str)
            or not isinstance(source.get('request_sha256'), str)
            or not _SHA256.fullmatch(source['request_sha256'])
            or not _aware_timestamp(source.get('captured_at'))):
        raise ValueError('Response-page metadata is incomplete or invalid')
    if owner_pattern is not None and (not isinstance(variables.get('id'), str)
                                      or not re.fullmatch(owner_pattern, variables['id'])):
        raise ValueError('Response page owner metadata is invalid')


def _checked_page_row_grain(rows, response_pages, envelope):
    """One raw row per response page with an exact preserved text and checksum."""
    rows_by_generation = {}
    for row in rows:
        if (not isinstance(row, dict) or set(row) != set(envelope)
                or not isinstance(row.get('file_id'), str) or row.get('record_index') != 1):
            raise ValueError('Raw rows must match envelope columns with record_index=1')
        generation = row['file_id']
        if generation not in response_pages or generation in rows_by_generation:
            raise ValueError('Raw rows must map one-to-one to response pages')
        text = row.get('record_text')
        if not isinstance(text, str) or row.get('payload') != text:
            raise ValueError('Raw row must preserve original JSON text')
        digest_value = hashlib.sha256(text.encode('utf-8')).hexdigest()
        if row.get('record_sha256') != digest_value or response_pages[generation].get('sha256') != digest_value:
            raise ValueError('Raw row or response page checksum mismatch')
        try:
            payload = json.loads(text)
        except (TypeError, ValueError, UnicodeError, RecursionError):
            raise ValueError('Response page is not valid JSON') from None
        if not isinstance(payload, dict) or payload.get('errors') or not isinstance(payload.get('data'), dict):
            raise ValueError('Response page is incomplete or has GraphQL errors')
        rows_by_generation[generation] = payload
    if set(rows_by_generation) != set(response_pages):
        raise ValueError('Raw rows omit a response page')
    return rows_by_generation


def _validate_klaviyo_events_page_publication(rows, files, stream=None):
    """Validate the Klaviyo events EVENT grain (one row per unique provider event).

    Raw pages remain the durable audit surface in GCS; the raw stream rows are
    event grain merged on (shop_key, object_gid) by the publication SQL, so the
    validator enforces event identity and provenance instead of page mapping.
    """
    if stream is not None and stream != 'events':
        raise ValueError('Unknown Klaviyo stream')
    response_pages, seals, generations = {}, [], set()
    for source in files:
        if not isinstance(source, dict):
            raise ValueError('Klaviyo manifest file must be an object')
        generation, sha256 = source.get('generation'), source.get('sha256')
        if (not isinstance(generation, str) or not generation.isdigit()
                or not isinstance(sha256, str) or not _SHA256.fullmatch(sha256)
                or not str(source.get('uri', '')).startswith('gs://')):
            raise ValueError('Klaviyo manifest file must include GCS URI, generation and SHA256')
        if generation in generations:
            raise ValueError('Klaviyo manifest generations must be unique')
        generations.add(generation)
        if source.get('role') == 'completion_seal':
            seals.append(source)
            continue
        if source.get('role') != 'response_page':
            raise ValueError('Klaviyo manifest has an unsupported file role')
        operation = source.get('operation')
        if (not isinstance(operation, str) or not operation
                or re.search(r'["\'\\,\n\r]', operation)):
            raise ValueError('Klaviyo response page operation is invalid')
        variables = source.get('variables')
        if not isinstance(variables, dict) or not variables:
            raise ValueError('Klaviyo response page metadata is incomplete or invalid')
        page_size = variables.get('page[size]')
        if set(variables) == {'cursor'}:
            cursor = variables['cursor']
            if not isinstance(cursor, str) or not cursor.startswith('https://a.klaviyo.com/api/events'):
                raise ValueError('Klaviyo response page cursor metadata is invalid')
        elif (set(variables) == {'page[size]', 'sort', 'include', 'filter'}
                and isinstance(page_size, int) and not isinstance(page_size, bool)
                and 1 <= page_size <= 200
                and variables.get('sort') == '-datetime'
                and variables.get('include') == 'profile'
                and isinstance(variables.get('filter'), str)
                and f'equals(metric_id,"{operation}")' in variables['filter']):
            pass
        else:
            raise ValueError('Klaviyo response page metadata is incomplete or invalid')
        if (not isinstance(source.get('request_sha256'), str)
                or not _SHA256.fullmatch(source['request_sha256'])
                or not _aware_timestamp(source.get('captured_at'))):
            raise ValueError('Klaviyo response page metadata is incomplete or invalid')
        if generation in response_pages:
            raise ValueError('Klaviyo response page generations must be unique')
        response_pages[generation] = source
    if len(seals) != 1:
        raise ValueError('Klaviyo manifest requires one completion seal')
    if not response_pages:
        counts = seals[0].get('klaviyo_counts')
        if not isinstance(counts, dict) or any(value != 0 for value in counts.values()):
            raise ValueError('Empty Klaviyo events stream requires a sealed zero count')

    rows_by_operation = {}
    seen_event_ids = set()
    columns = {column.name: column for column in _KLAVIYO_EVENT_CONTRACT.columns}
    for row in rows:
        if not isinstance(row, dict) or set(row) != set(columns):
            raise ValueError('Klaviyo event rows must match the event contract columns')
        for name, column in columns.items():
            value = row[name]
            if value is None:
                if column.required:
                    raise ValueError(f'Klaviyo event row is missing required column {name}')
                continue
            if not isinstance(value, str):
                raise ValueError(f'Klaviyo event row column {name} must be a stage string')
        try:
            event = json.loads(row['original_payload'])
        except (TypeError, ValueError, UnicodeError, RecursionError):
            raise ValueError('Klaviyo event row original_payload is not valid JSON') from None
        if (not isinstance(event, dict) or not isinstance(event.get('id'), str) or not event['id']
                or row['event_gid'] != event['id']):
            raise ValueError('Klaviyo event row payload does not match its event identity')
        if row['event_gid'] in seen_event_ids:
            raise ValueError('Klaviyo event rows must be unique per event identity within one extraction')
        seen_event_ids.add(row['event_gid'])
        metric = (event.get('relationships', {}).get('metric', {}).get('data') or {}).get('id')
        if not metric or metric != row['metric_id'] or metric not in {s['operation'] for s in response_pages.values()}:
            raise ValueError('Klaviyo event row references a page of another metric')
        rows_by_operation[metric] = rows_by_operation.get(metric, 0) + 1
    counts = seals[0].get('klaviyo_counts') if seals else None
    if isinstance(counts, dict):
        for metric_id, expected in counts.items():
            if rows_by_operation.get(metric_id, 0) != expected:
                raise ValueError('Klaviyo raw rows omit events of a sealed metric count')


_KLAVIYO_CAMPAIGN_INCLUDED_TYPES = ('campaign', 'campaign-audience', 'campaign-message',
                                    'campaign-variation')
_KLAVIYO_CAMPAIGNS_LIST_VARIABLES = {'page[size]', 'sort', 'include', 'filter'}
_KLAVIYO_MESSAGES_LIST_VARIABLES = {'page[size]', 'sort', 'include'}


def _klaviyo_campaign_page_operation(source):
    """Validate one response page's operation/variables metadata; return the operation."""
    operation = source.get('operation')
    if operation not in ('campaigns_list', 'messages_list'):
        raise ValueError('Klaviyo campaigns response page operation is invalid')
    variables = source.get('variables')
    if not isinstance(variables, dict) or not variables:
        raise ValueError('Klaviyo response page metadata is incomplete or invalid')
    page_size = variables.get('page[size]')
    if set(variables) == {'cursor'}:
        cursor = variables['cursor']
        origin = ('https://a.klaviyo.com/api/campaigns' if operation == 'campaigns_list'
                  else 'https://a.klaviyo.com/api/campaign-messages')
        if not isinstance(cursor, str) or not cursor.startswith(origin):
            raise ValueError('Klaviyo response page cursor metadata is invalid')
    elif operation == 'campaigns_list' and set(variables) in (
            _KLAVIYO_CAMPAIGNS_LIST_VARIABLES, _KLAVIYO_CAMPAIGNS_LIST_VARIABLES - {'filter'}):
        if (isinstance(page_size, int) and not isinstance(page_size, bool)
                and 1 <= page_size <= 100
                and variables.get('sort') == '-updated_at'
                and variables.get('include') == 'campaign-audiences,campaign-messages'
                and variables.get('filter', 'equals(archived,false)')
                in ('equals(archived,false)', 'equals(archived,true)')):
            pass
        else:
            raise ValueError('Klaviyo response page metadata is incomplete or invalid')
    elif operation == 'messages_list' and set(variables) == _KLAVIYO_MESSAGES_LIST_VARIABLES:
        if (isinstance(page_size, int) and not isinstance(page_size, bool)
                and 1 <= page_size <= 100
                and variables.get('sort') == '-updated'
                and variables.get('include') == 'campaign,campaign-variations'):
            pass
        else:
            raise ValueError('Klaviyo response page metadata is incomplete or invalid')
    else:
        raise ValueError('Klaviyo response page metadata is incomplete or invalid')
    if (not isinstance(source.get('request_sha256'), str)
            or not _SHA256.fullmatch(source['request_sha256'])
            or not _aware_timestamp(source.get('captured_at'))):
        raise ValueError('Klaviyo response page metadata is incomplete or invalid')
    return operation


def _klaviyo_campaign_page_hierarchy(operation, payload):
    """Validate the resource hierarchy of one campaigns-stream page, fail closed."""
    included = payload.get('included', [])
    if not isinstance(included, list):
        raise ValueError('Klaviyo response page included collection is invalid')
    campaigns, audiences, messages, variations = set(), set(), set(), set()
    for item in included:
        if not isinstance(item, dict) or item.get('type') not in _KLAVIYO_CAMPAIGN_INCLUDED_TYPES:
            raise ValueError('Klaviyo response page contains an unexpected included resource')
        if not isinstance(item.get('id'), str) or not item['id']:
            raise ValueError('Klaviyo included resource is missing its identity')
        if not isinstance(item.get('relationships'), dict):
            raise ValueError('Klaviyo included resource is missing its relationships')
        {'campaign': campaigns, 'campaign-audience': audiences,
         'campaign-message': messages, 'campaign-variation': variations}[item['type']].add(item['id'])
    data_ids = set()
    root_type = 'campaign' if operation == 'campaigns_list' else 'campaign-message'
    for resource in payload['data']:
        if (not isinstance(resource, dict) or resource.get('type') != root_type
                or not isinstance(resource.get('id'), str) or not resource['id']):
            raise ValueError('Klaviyo response page contains an unidentified resource')
        data_ids.add(resource['id'])
    if operation == 'campaigns_list':
        campaigns |= data_ids
        for item in included:
            parent = item['relationships'].get('campaign' if item['type'] == 'campaign-audience'
                                               else 'campaign')
            parent_data = parent.get('data') if isinstance(parent, dict) else None
            if not isinstance(parent_data, dict) or parent_data.get('id') not in campaigns:
                raise ValueError('Klaviyo included resource references a parent missing from the page')
            if item['type'] == 'campaign-message':
                parent = item['relationships'].get('campaign-audience')
                parent_data = parent.get('data') if isinstance(parent, dict) else None
                if not isinstance(parent_data, dict) or parent_data.get('id') not in audiences:
                    raise ValueError('Klaviyo message references an audience missing from the page')
    else:
        messages |= data_ids
        for item in included:
            if item['type'] == 'campaign-variation':
                parent = item['relationships'].get('campaign-message')
                parent_data = parent.get('data') if isinstance(parent, dict) else None
                if not isinstance(parent_data, dict) or parent_data.get('id') not in messages:
                    raise ValueError('Klaviyo variation references a message missing from the page')
            elif item['type'] == 'campaign-message':
                parent = item['relationships'].get('campaign')
                parent_data = parent.get('data') if isinstance(parent, dict) else None
                if not isinstance(parent_data, dict) or parent_data.get('id') not in campaigns:
                    raise ValueError('Klaviyo message references a campaign missing from the page')


def _validate_klaviyo_campaigns_page_publication(rows, files, stream=None):
    """Validate the Klaviyo campaigns snapshot page grain (JSON:API, one page per row)."""
    if stream is not None and stream != 'campaigns':
        raise ValueError('Unknown Klaviyo stream')
    response_pages, seals, generations = {}, [], set()
    for source in files:
        if not isinstance(source, dict):
            raise ValueError('Klaviyo manifest file must be an object')
        generation, sha256 = source.get('generation'), source.get('sha256')
        if (not isinstance(generation, str) or not generation.isdigit()
                or not isinstance(sha256, str) or not _SHA256.fullmatch(sha256)
                or not str(source.get('uri', '')).startswith('gs://')):
            raise ValueError('Klaviyo manifest file must include GCS URI, generation and SHA256')
        if generation in generations:
            raise ValueError('Klaviyo manifest generations must be unique')
        generations.add(generation)
        if source.get('role') == 'completion_seal':
            seals.append(source)
            continue
        if source.get('role') != 'response_page':
            raise ValueError('Klaviyo manifest has an unsupported file role')
        operation = _klaviyo_campaign_page_operation(source)
        if generation in response_pages:
            raise ValueError('Klaviyo response page generations must be unique')
        response_pages[generation] = source
    if len(seals) != 1:
        raise ValueError('Klaviyo manifest requires one completion seal')
    if not response_pages:
        counts = seals[0].get('klaviyo_counts')
        if not isinstance(counts, dict) or any(value != 0 for value in counts.values()):
            raise ValueError('Empty Klaviyo campaigns stream requires a sealed zero count')

    rows_by_generation = {}
    for row in rows:
        if (not isinstance(row, dict) or set(row) != set(contract_columns()[0])
                or not isinstance(row.get('file_id'), str) or row.get('record_index') != 1):
            raise ValueError('Klaviyo response pages require envelope columns and record_index=1')
        generation = row['file_id']
        if generation not in response_pages or generation in rows_by_generation:
            raise ValueError('Klaviyo raw rows must map one-to-one to response pages')
        text = row.get('record_text')
        if not isinstance(text, str) or row.get('payload') != text:
            raise ValueError('Klaviyo raw row must preserve original JSON text')
        digest_value = hashlib.sha256(text.encode('utf-8')).hexdigest()
        if row.get('record_sha256') != digest_value or response_pages[generation].get('sha256') != digest_value:
            raise ValueError('Klaviyo raw row or response page checksum mismatch')
        try:
            payload = json.loads(text)
        except (TypeError, ValueError, UnicodeError, RecursionError):
            raise ValueError('Klaviyo response page is not valid JSON') from None
        if not isinstance(payload, dict) or payload.get('errors') or not isinstance(payload.get('data'), list):
            raise ValueError('Klaviyo response page is incomplete or has errors')
        rows_by_generation[generation] = payload
    if set(rows_by_generation) != set(response_pages):
        raise ValueError('Klaviyo raw rows omit a response page')
    for generation, payload in rows_by_generation.items():
        _klaviyo_campaign_page_hierarchy(response_pages[generation].get('operation'),
                                         payload)



def _checked_connection(connection):
    if (not isinstance(connection, dict) or not isinstance(connection.get('pageInfo'), dict)
            or type(connection['pageInfo'].get('hasNextPage')) is not bool
            or (connection['pageInfo'].get('endCursor') is not None
                and not isinstance(connection['pageInfo'].get('endCursor'), str))
            or not isinstance(connection.get('edges'), list)):
        raise ValueError('Response page is missing its connection')
    if connection['pageInfo']['hasNextPage'] and not connection['pageInfo'].get('endCursor'):
        raise ValueError('Response page has a nonadvancing cursor')


def _validate_payments_page_publication(rows, files, stream):
    """Validate tender/balance/dispute root-connection page grain before any write."""
    operation = _PAYMENT_STREAM_OPERATIONS.get(stream)
    if operation is None:
        raise ValueError('Unknown payments stream')
    response_pages, seals = _checked_page_files(files, {operation})
    for source in response_pages.values():
        if source.get('operation') != operation:
            raise ValueError('Payments stream and response operation do not match')
        expected = {'first', 'after', 'query'} if operation in _PAYMENT_QUERY_OPERATIONS else {'first', 'after'}
        _checked_page_variables(source, expected, query_operation=operation in _PAYMENT_QUERY_OPERATIONS)
    if len(seals) != 1:
        raise ValueError('Payments manifest requires one completion seal')
    if not response_pages:
        counts = seals[0].get('payments_counts')
        if not isinstance(counts, dict) or counts.get(operation) != 0:
            raise ValueError('Empty payments stream requires a sealed zero count')
    rows_by_generation = _checked_page_row_grain(rows, response_pages, contract_columns()[0])
    for payload in rows_by_generation.values():
        if operation == 'tenderTransactions':
            connection = payload['data'].get(operation)
        else:
            account = payload['data'].get('shopifyPaymentsAccount')
            if not isinstance(account, dict):
                raise ValueError('Payments response page is missing its account')
            connection = account.get(operation)
        _checked_connection(connection)


def _validate_fulfillments_page_publication(rows, files):
    """Validate orders/fulfillments page grain before any write."""
    response_pages, seals = _checked_page_files(files, _FULFILLMENT_OPERATIONS)
    for source in response_pages.values():
        operation = source['operation']
        if operation == 'orders':
            _checked_page_variables(source, {'first', 'after', 'query'}, query_operation=True)
        else:
            _checked_page_variables(source, {'id'}, owner_pattern=r'gid://shopify/Order/[0-9]+')
    if len(seals) != 1 or not response_pages:
        raise ValueError('Fulfillments manifest requires one completion seal and response pages')
    rows_by_generation = _checked_page_row_grain(rows, response_pages, contract_columns()[0])
    for generation, payload in rows_by_generation.items():
        source = response_pages[generation]
        if source['operation'] == 'orders':
            _checked_connection(payload['data'].get('orders'))
            continue
        owner = source['variables'].get('id')
        node = payload['data'].get('node')
        if not isinstance(owner, str) or not isinstance(node, dict) or node.get('id') != owner:
            raise ValueError('Fulfillments response page owner does not match its request')
        fulfillments = node.get('fulfillments')
        if (not isinstance(fulfillments, list)
                or any(not isinstance(item, dict) or not isinstance(item.get('id'), str)
                       for item in fulfillments)):
            raise ValueError('Fulfillments response page does not contain identified fulfillment objects')


def _validate_inventory_page_publication(rows, files, stream):
    """Validate inventory items/levels page grain before any write."""
    allowed = _INVENTORY_STREAM_OPERATIONS.get(stream)
    if allowed is None:
        raise ValueError('Unknown inventory stream')
    response_pages, seals = _checked_page_files(files, set(allowed))
    for source in response_pages.values():
        operation = source.get('operation')
        if operation == 'inventoryItems':
            _checked_page_variables(source, {'first', 'after', 'query'}, query_operation=True)
        elif operation == 'locations':
            _checked_page_variables(source, {'first', 'after'})
        else:
            _checked_page_variables(source, {'first', 'after', 'id'},
                                    owner_pattern=r'gid://shopify/Location/[0-9]+')
    if len(seals) != 1:
        raise ValueError('Inventory manifest requires one completion seal')
    if not response_pages:
        counts = seals[0].get('inventory_counts')
        if (not isinstance(counts, dict) or any(counts.get(operation) != 0 for operation in allowed)):
            raise ValueError('Empty inventory stream requires sealed zero counts')
    rows_by_generation = _checked_page_row_grain(rows, response_pages, contract_columns()[0])
    for generation, payload in rows_by_generation.items():
        source = response_pages[generation]
        operation = source['operation']
        if operation == 'inventoryLevels':
            node = payload['data'].get('node')
            owner = source['variables'].get('id')
            if not isinstance(owner, str) or not isinstance(node, dict) or node.get('id') != owner:
                raise ValueError('Inventory response page owner does not match its request')
            _checked_connection(node.get('inventoryLevels'))
        else:
            _checked_connection(payload['data'].get(operation))


def _validate_fulfillment_orders_page_publication(rows, files, stream):
    """Validate fulfillment-order root pages or owner-scoped line pages."""
    allowed = _FULFILLMENT_ORDER_STREAM_OPERATIONS.get(stream)
    if allowed is None:
        raise ValueError('Unknown fulfillment-order stream')
    response_pages, seals = _checked_page_files(files, set(allowed))
    for source in response_pages.values():
        if source['operation'] == 'fulfillmentOrders':
            _checked_page_variables(source, {'first', 'after'})
        else:
            _checked_page_variables(source, {'first', 'after', 'id'},
                                    owner_pattern=r'gid://shopify/FulfillmentOrder/[0-9]+')
    if len(seals) != 1:
        raise ValueError('Fulfillment-order manifest requires one completion seal')
    if not response_pages:
        counts = seals[0].get('fulfillment_order_counts')
        expected = 'fulfillmentOrders' if stream == 'fulfillment_orders' else 'lineItems'
        if not isinstance(counts, dict) or counts.get(expected) != 0:
            raise ValueError('Empty fulfillment-order stream requires a sealed zero count')
    rows_by_generation = _checked_page_row_grain(rows, response_pages, contract_columns()[0])
    for generation, payload in rows_by_generation.items():
        source = response_pages[generation]
        if source['operation'] == 'fulfillmentOrders':
            _checked_connection(payload['data'].get('fulfillmentOrders'))
        else:
            node = payload['data'].get('node')
            owner = source['variables'].get('id')
            if not isinstance(node, dict) or node.get('id') != owner:
                raise ValueError('Fulfillment-order line page owner does not match its request')
            _checked_connection(node.get('lineItems'))


CONTRACT = Path(__file__).resolve().parents[2] / 'warehouse/contracts/shopify_raw_v1.yaml'


def contract_columns():
    contract = yaml.safe_load(CONTRACT.read_text())
    raw = {k: v['type'] for k, v in contract['record_envelope']['columns'].items()}
    return raw, contract['run_manifest']['columns']


def dataset_id(value):
    if not re.fullmatch(r'[a-z][a-z0-9-]{4,61}[a-z0-9]\.[A-Za-z_][A-Za-z0-9_]*', value):
        raise ValueError('Expected project.dataset identifier')
    return value


def _publication_contract(dataset, stream):
    dataset_id(dataset)
    if stream not in ('orders', 'order_refunds', 'returns', 'customers', 'products', 'variants',
                      'tender_transactions', 'balance_transactions', 'order_transactions', 'disputes', 'fulfillments',
                      'fulfillment_orders', 'fulfillment_order_line_items',
                      'inventory_items', 'inventory_levels', 'events', 'campaigns', 'acceptance',
                      'metafield_orders', 'metafield_products', 'metafield_product_variants'):
        raise ValueError('Stream has no publication contract')
    if stream == 'events':
        # Event grain: the Klaviyo events stream leaves the shared page envelope
        # and follows the executable event contract.
        raw = {column.name: column.type for column in _KLAVIYO_EVENT_CONTRACT.columns}
        return raw, contract_columns()[1]
    return contract_columns()


def _publish_klaviyo_event_rows(client, dataset, manifest, rows):
    """Event-grain Klaviyo publication: all-string stage load, identity MERGE, manifest transaction.

    The stage loads every column as STRING (canonical strings produced by the
    flatten); the MERGE casts explicitly. The MERGE is atomic and its result is
    awaited before the manifest transaction, so a failed rows phase leaves no
    manifest and the rows stay invisible to consumers; a replay re-MERGEs
    nothing (events are immutable) and re-publishes an identical manifest.
    """
    stage = '_load_' + uuid.uuid4().hex
    _, fields = contract_columns()
    table = bigquery.Table(f'{dataset}.{stage}', schema=_KLAVIYO_EVENT_CONTRACT.stage_fields())
    table.expires = datetime.now(timezone.utc) + timedelta(hours=24)
    client.create_table(table)
    load = None
    if rows:
        with tempfile.SpooledTemporaryFile(max_size=4*1024*1024, mode='r+b') as data:
            count = 0
            for row in rows:
                data.write((json.dumps(row, ensure_ascii=False) + '\n').encode())
                count += 1
            if count != manifest['raw_record_count']:
                raise ValueError('Parsed event count does not match validated manifest')
            data.seek(0)
            load = client.load_table_from_file(data, table.reference,
                job_config=bigquery.LoadJobConfig(source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
                                                 schema=table.schema, write_disposition='WRITE_EMPTY'))
            print(json.dumps({'event': 'raw_load_submitted', 'job_id': load.job_id, 'stage': stage}), flush=True)
            load.result(timeout=300)
    params = [bigquery.ScalarQueryParameter('m_' + k, 'STRING' if t == 'JSON' else t,
               json.dumps(manifest[k]) if t == 'JSON' else manifest[k]) for k, t in fields.items()]
    if rows:
        rows_sql = publication_rows_sql(dataset, 'events', stage)
        rows_params = [p for p in params if p.name in ('m_shop_key', 'm_extraction_id')]
        rows_job = client.query(rows_sql, job_config=bigquery.QueryJobConfig(
            query_parameters=rows_params, maximum_bytes_billed=1073741824,
            labels={'purpose': 'raw_publication'}))
        print(json.dumps({'event': 'raw_rows_submitted', 'job_id': rows_job.job_id}), flush=True)
        rows_job.result(timeout=300)
    job = client.query(publication_sql(dataset, 'events', stage), job_config=bigquery.QueryJobConfig(
        query_parameters=params, maximum_bytes_billed=1073741824,
        labels={'purpose': 'raw_publication'}))
    print(json.dumps({'event': 'raw_publication_submitted', 'job_id': job.job_id}), flush=True)
    job.result(timeout=300)
    return {'load_job_id': load.job_id if load else None, 'publication_job_id': job.job_id,
            'stage_table': f'{dataset}.{stage}', 'published': True}


def publication_rows_sql(dataset, stream, stage):
    """Idempotent row publication; runs outside the publication transaction.

    A DML statement inside a multi-statement transaction is subject to a ~16 MiB
    per-statement bytes-billed cap, while an INSERT bills the full size of the
    partitions it modifies. Raw consumers only see rows after the manifest is
    published by publication_sql, so this phase is invisible on its own and
    idempotent on replay.

    The events stream is event grain: rows merge on the provider event identity
    (shop_key, object_gid), so re-captures of overlapping windows insert nothing
    instead of accumulating duplicate observations.
    """
    raw, _ = _publication_contract(dataset, stream)
    if not re.fullmatch('_load_[0-9a-f]{32}', stage):
        raise ValueError('Invalid staging identifier')
    fields = ', '.join(raw)
    select = ', '.join(
        f"PARSE_JSON(s.{k}, wide_number_mode=>'round')" if t == 'JSON' else f's.{k}'
        for k, t in raw.items())
    if stream == 'events':
        # Every USING expression needs an explicit alias: unnamed CAST/PARSE_JSON
        # columns get BigQuery auto-names (f0_, f1_...) and the VALUES clause
        # references them as S.<column>.
        cast = {'JSON': "PARSE_JSON(s.{0}) AS {0}",
                'TIMESTAMP': "CAST(s.{0} AS TIMESTAMP) AS {0}",
                'INT64': "CAST(s.{0} AS INT64) AS {0}"}
        values = ', '.join((cast[t].format(k) if t in cast else f's.{k} AS {k}') for k, t in raw.items())
        return f'''
MERGE `{dataset}.{stream}` T
USING (SELECT {values} FROM `{dataset}.{stage}` s
WHERE s.shop_key = @m_shop_key AND s.source_extraction_id = @m_extraction_id) S
ON T.shop_key = S.shop_key AND T.event_gid = S.event_gid
WHEN NOT MATCHED BY TARGET THEN INSERT ({fields})
VALUES ({', '.join('S.' + k for k in raw)});'''
    return f'''
INSERT INTO `{dataset}.{stream}` ({fields})
SELECT {select} FROM `{dataset}.{stage}` s
WHERE s.shop_key = @m_shop_key AND s.extraction_id = @m_extraction_id
  AND NOT EXISTS(SELECT 1 FROM `{dataset}.{stream}` t
    WHERE t.shop_key = @m_shop_key AND t.extraction_id = @m_extraction_id
      AND t.file_id = s.file_id AND t.record_index = s.record_index);
'''


def publication_sql(dataset, stream, stage):
    raw, manifest = _publication_contract(dataset, stream)
    if not re.fullmatch('_load_[0-9a-f]{32}', stage):
        raise ValueError('Invalid staging identifier')
    if stream == 'events':
        return _events_publication_sql(dataset, stage, raw, manifest)
    raw_fields = ', '.join(raw)
    # wide_number_mode='round': provider JSON may carry decimal literals that exceed
    # float64 round-trip precision (e.g. geolocation -117.12157500000001). The parsed
    # JSON column is a query convenience; record_text stays the authoritative original.
    # The transaction only holds and compares SMALL columns: record_text and
    # payload are megabytes per record, so materializing them in the candidate
    # temp table and comparing record_text made every transaction statement
    # bill a full ~490 MiB stage scan, overrunning the transaction's cumulative
    # bytes-billed budget. record_text content is bound to record_sha256 by the
    # raw envelope (validated before publication), so comparing record_sha256
    # is an equivalent conflict check. The bulk row INSERT (which must read the
    # heavy columns) runs outside the transaction as publication_rows_sql.
    compare = ' OR '.join(f't.{k} IS DISTINCT FROM s.{k}'
                          for k in raw if k not in ('payload', 'record_text', 'ingested_at'))
    small = ', '.join(k for k in raw if k not in ('payload', 'record_text'))
    manifest_values = ', '.join(f'PARSE_JSON(@m_{k})' if t == 'JSON' else f'@m_{k}' for k, t in manifest.items())
    return f'''
BEGIN TRANSACTION;
-- A real write to the pre-existing singleton forces concurrent publishers to
-- conflict/abort instead of both inserting an absent key under snapshot isolation.
UPDATE `{dataset}._publication_guard` SET epoch = epoch + 1 WHERE TRUE;
ASSERT @@row_count = 1 AS 'Publication guard must contain exactly one row';
CREATE TEMP TABLE candidate AS SELECT {small} FROM `{dataset}.{stage}`;
ASSERT (SELECT COUNT(*) FROM candidate) = @m_raw_record_count AS 'Raw count mismatch';
ASSERT NOT EXISTS(SELECT 1 FROM candidate WHERE shop_key != @m_shop_key
  OR extraction_id != @m_extraction_id OR query_sha256 != @m_query_sha256
  OR request_sha256 != @m_request_sha256 OR api_version != @m_actual_api_version)
  AS 'Record/manifest identity mismatch';
ASSERT NOT EXISTS(SELECT 1 FROM candidate GROUP BY shop_key, extraction_id,
  file_id, record_index HAVING COUNT(*) > 1) AS 'Duplicate candidate key';
-- Existence/conflict checks against the target are written with static
-- @m_shop_key/@m_extraction_id predicates: the identity assert above already
-- guarantees every candidate row shares that identity, so this is semantically
-- identical to joining on the candidate columns — and it lets BigQuery prune
-- by (shop_key, extraction_id) clustering. The unprunable join-vs-candidate
-- form scanned the whole growing raw table per publication and failed on the
-- per-statement bytes-billed cap once the table passed ~340 MiB (2026-09-13,
--   habibi 2022-2024 backfill window A).
ASSERT NOT EXISTS(SELECT 1 FROM `{dataset}.{stream}` t JOIN candidate s
  ON t.file_id = s.file_id AND t.record_index = s.record_index
  WHERE t.shop_key = @m_shop_key AND t.extraction_id = @m_extraction_id
    AND {compare}) AS 'Conflicting replay record';
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
-- The bulk row INSERT deliberately lives OUTSIDE this transaction (it runs as
-- its own standalone DML job before this one): a DML statement inside a
-- multi-statement transaction is subject to a ~16 MiB per-statement bytes
-- billed cap, while an INSERT bills the full size of the partitions it
-- modifies — the same-day partition of raw_shopify.orders passed 340 MiB after
-- the 2025/2026-YTD extractions (2026-09-13). Consumers only see rows once the
-- manifest is published here, so a crash between the two phases leaves rows
-- invisible and a retry is idempotent.
ASSERT (SELECT COUNT(*) FROM `{dataset}.{stream}` WHERE shop_key = @m_shop_key
  AND extraction_id = @m_extraction_id) = @m_raw_record_count AS 'Extraction has unexpected rows';
INSERT INTO `{dataset}.ingestion_runs` ({', '.join(manifest)})
SELECT {manifest_values} FROM UNNEST([1]) WHERE NOT EXISTS(SELECT 1 FROM `{dataset}.ingestion_runs`
  WHERE shop_key = @m_shop_key AND stream = @m_stream AND extraction_id = @m_extraction_id);
COMMIT TRANSACTION;
'''


def _events_publication_sql(dataset, stage, raw, manifest):
    """Event-grain Klaviyo manifest transaction.

    The rows MERGE already ran as its own awaited job: it is atomic, so a
    failed rows phase never reaches this transaction and a replay re-MERGEs
    nothing. Consumers only see rows once the manifest is published here, so
    the pre-merge visibility invariant holds without a target-coverage assert
    (the unprunable probe form is what overran the bytes cap on Shopify raw).
    """
    raw_fields = ', '.join(raw)
    small = ', '.join(k for k in raw if k != 'original_payload')
    manifest_values = ', '.join(f'PARSE_JSON(@m_{k})' if t == 'JSON' else f'@m_{k}' for k, t in manifest.items())
    return f'''
BEGIN TRANSACTION;
-- A real write to the pre-existing singleton forces concurrent publishers to
-- conflict/abort instead of both inserting an absent key under snapshot isolation.
UPDATE `{dataset}._publication_guard` SET epoch = epoch + 1 WHERE TRUE;
ASSERT @@row_count = 1 AS 'Publication guard must contain exactly one row';
CREATE TEMP TABLE candidate AS SELECT {small} FROM `{dataset}.{stage}`;
ASSERT (SELECT COUNT(*) FROM candidate) = @m_raw_record_count AS 'Raw count mismatch';
ASSERT NOT EXISTS(SELECT 1 FROM candidate WHERE shop_key != @m_shop_key
  OR source_extraction_id != @m_extraction_id)
  AS 'Record/manifest identity mismatch';
ASSERT NOT EXISTS(SELECT 1 FROM candidate GROUP BY shop_key, event_gid HAVING COUNT(*) > 1)
  AS 'Duplicate candidate key';
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
INSERT INTO `{dataset}.ingestion_runs` ({', '.join(manifest)})
SELECT {manifest_values} FROM UNNEST([1]) WHERE NOT EXISTS(SELECT 1 FROM `{dataset}.ingestion_runs`
  WHERE shop_key = @m_shop_key AND stream = @m_stream AND extraction_id = @m_extraction_id);
COMMIT TRANSACTION;
'''


def initialize_tables(client, dataset, stream):
    dataset_id(dataset)
    # Validate stream through the same whitelist as the transaction.
    publication_sql(dataset, stream, '_load_' + '0'*32)
    _, manifest = contract_columns()
    if stream == 'events':
        contract = _KLAVIYO_EVENT_CONTRACT
        fields = {column.name: column.type for column in contract.columns}
        schema = contract.bigquery_fields()
        cluster = list(contract.cluster_by)
    else:
        raw, _ = contract_columns()
        fields = raw
        schema = [bigquery.SchemaField(k, t, mode='REQUIRED' if k not in ('object_gid', 'parent_gid') else 'NULLABLE')
                  for k, t in raw.items()]
        cluster = ['shop_key', 'extraction_id']
    table = bigquery.Table(f'{dataset}.{stream}', schema=schema)
    table.time_partitioning = bigquery.TimePartitioning(field='ingested_at')
    table.clustering_fields = cluster
    client.create_table(table, exists_ok=True)
    actual = client.get_table(table.reference)
    aliases = {'INTEGER': 'INT64'}
    if {f.name: aliases.get(f.field_type, f.field_type) for f in actual.schema} != fields:
        raise ValueError(f'Existing table schema does not match contract: {stream}')
    manifest_table = bigquery.Table(f'{dataset}.ingestion_runs', schema=[
        bigquery.SchemaField(k, t, mode='NULLABLE') for k, t in manifest.items()])
    manifest_table.time_partitioning = bigquery.TimePartitioning(field='published_at')
    client.create_table(manifest_table, exists_ok=True)
    actual = client.get_table(manifest_table.reference)
    if {f.name: aliases.get(f.field_type, f.field_type) for f in actual.schema} != manifest:
        raise ValueError('Existing table schema does not match contract: ingestion_runs')
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
    if stream == 'order_refunds' and manifest['transport'] == 'shopify_bulk_and_graphql_pages_v2':
        from .refund_publication_v2 import validate_refund_publication
        records = list(records)
        validate_refund_publication(records, files)
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
    if stream == 'returns' and manifest['transport'] == 'shopify_bulk_query':
        from .returns_publication_v2 import validate_returns_publication
        records = list(records)
        validate_returns_publication(records, files)
    if stream == 'returns' and manifest['transport'] not in ('shopify_graphql_pages', 'shopify_bulk_query'):
        raise ValueError('Returns publication requires shopify_graphql_pages or shopify_bulk_query transport')
    if stream == 'returns' and manifest['transport'] == 'shopify_graphql_pages':
        return_rows = []
        for row in records:
            if not isinstance(row, dict) or set(row) != set(raw):
                raise ValueError('Raw row must match envelope columns')
            if row['file_id'] not in file_ids or row['payload'] != row['record_text']:
                raise ValueError('Raw row must preserve its referenced file and original JSON')
            return_rows.append(row)
        _validate_returns_page_publication(return_rows, files)
        records = return_rows
    bulk_engine = stream in ('customers', 'products', 'variants', 'fulfillments',
                            'fulfillment_orders', 'fulfillment_order_line_items',
                            'inventory_items', 'inventory_levels') and manifest['transport'] in (
                                'shopify_bulk_query', 'shopify_bulk_with_country_codes')
    if bulk_engine:
        from .bulk_engine import validate_family_publication
        records = list(records)
        validate_family_publication(stream, records, files, manifest)
    if stream in ('customers', 'products', 'variants') and not bulk_engine:
        if manifest['transport'] != 'shopify_graphql_pages':
            raise ValueError('Catalog publication requires shopify_graphql_pages transport')
        catalog_rows = []
        for row in records:
            if not isinstance(row, dict) or set(row) != set(raw):
                raise ValueError('Raw row must match envelope columns')
            if row['file_id'] not in file_ids or row['payload'] != row['record_text']:
                raise ValueError('Raw row must preserve its referenced file and original JSON')
            catalog_rows.append(row)
        _validate_catalog_page_publication(catalog_rows, files, stream)
        records = catalog_rows
    if stream in ('tender_transactions', 'balance_transactions', 'disputes'):
        if manifest['transport'] != 'shopify_graphql_pages':
            raise ValueError('Payments publication requires shopify_graphql_pages transport')
        payment_rows = []
        for row in records:
            if not isinstance(row, dict) or set(row) != set(raw):
                raise ValueError('Raw row must match envelope columns')
            if row['file_id'] not in file_ids or row['payload'] != row['record_text']:
                raise ValueError('Raw row must preserve its referenced file and original JSON')
            payment_rows.append(row)
        _validate_payments_page_publication(payment_rows, files, stream)
        records = payment_rows
    if stream == 'fulfillments' and not bulk_engine:
        if manifest['transport'] != 'shopify_graphql_pages':
            raise ValueError('Fulfillments publication requires shopify_graphql_pages transport')
        fulfillment_rows = []
        for row in records:
            if not isinstance(row, dict) or set(row) != set(raw):
                raise ValueError('Raw row must match envelope columns')
            if row['file_id'] not in file_ids or row['payload'] != row['record_text']:
                raise ValueError('Raw row must preserve its referenced file and original JSON')
            fulfillment_rows.append(row)
        _validate_fulfillments_page_publication(fulfillment_rows, files)
        records = fulfillment_rows
    if stream in ('fulfillment_orders', 'fulfillment_order_line_items') and not bulk_engine:
        if manifest['transport'] != 'shopify_graphql_pages':
            raise ValueError('Fulfillment-order publication requires shopify_graphql_pages transport')
        fulfillment_order_rows = []
        for row in records:
            if not isinstance(row, dict) or set(row) != set(raw):
                raise ValueError('Raw row must match envelope columns')
            if row['file_id'] not in file_ids or row['payload'] != row['record_text']:
                raise ValueError('Raw row must preserve its referenced file and original JSON')
            fulfillment_order_rows.append(row)
        _validate_fulfillment_orders_page_publication(fulfillment_order_rows, files, stream)
        records = fulfillment_order_rows
    if stream in ('inventory_items', 'inventory_levels') and not bulk_engine:
        if manifest['transport'] != 'shopify_graphql_pages':
            raise ValueError('Inventory publication requires shopify_graphql_pages transport')
        inventory_rows = []
        for row in records:
            if not isinstance(row, dict) or set(row) != set(raw):
                raise ValueError('Raw row must match envelope columns')
            if row['file_id'] not in file_ids or row['payload'] != row['record_text']:
                raise ValueError('Raw row must preserve its referenced file and original JSON')
            inventory_rows.append(row)
        _validate_inventory_page_publication(inventory_rows, files, stream)
        records = inventory_rows
    if stream == 'events':
        if manifest['transport'] != 'klaviyo_jsonapi_pages':
            raise ValueError('Klaviyo events publication requires klaviyo_jsonapi_pages transport')
        event_rows = list(records)
        _validate_klaviyo_events_page_publication(event_rows, files, stream)
        return _publish_klaviyo_event_rows(client, dataset, manifest, event_rows)
    if stream == 'campaigns':
        if manifest['transport'] != 'klaviyo_jsonapi_pages':
            raise ValueError('Klaviyo campaigns publication requires klaviyo_jsonapi_pages transport')
        campaign_rows = []
        for row in records:
            if not isinstance(row, dict) or set(row) != set(raw):
                raise ValueError('Raw row must match envelope columns')
            if row['file_id'] not in file_ids or row['payload'] != row['record_text']:
                raise ValueError('Raw row must preserve its referenced file and original JSON')
            campaign_rows.append(row)
        _validate_klaviyo_campaigns_page_publication(campaign_rows, files, stream)
        records = campaign_rows
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
    if count:
        rows_sql = publication_rows_sql(dataset, stream, stage)
        rows_params = [p for p in params if p.name in ('m_shop_key', 'm_extraction_id')]
        rows_job = client.query(rows_sql, job_config=bigquery.QueryJobConfig(
            query_parameters=rows_params, maximum_bytes_billed=1073741824,
            labels={'purpose': 'raw_publication'}))
        print(json.dumps({'event': 'raw_rows_submitted', 'job_id': rows_job.job_id}), flush=True)
        rows_job.result(timeout=300)
    job = client.query(sql, job_config=bigquery.QueryJobConfig(
        query_parameters=params, maximum_bytes_billed=1073741824,
        labels={'purpose': 'raw_publication'}))
    print(json.dumps({'event': 'raw_publication_submitted', 'job_id': job.job_id}), flush=True)
    job.result(timeout=300)
    return {'load_job_id': load.job_id if load else None, 'publication_job_id': job.job_id,
            'stage_table': f'{dataset}.{stage}', 'published': True}
