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
_CATALOG_OPERATIONS = {'customers', 'products', 'variants'}
_PAYMENT_STREAM_OPERATIONS = {'tender_transactions': 'tenderTransactions',
                              'balance_transactions': 'balanceTransactions',
                              'disputes': 'disputes'}
_PAYMENT_QUERY_OPERATIONS = {'tenderTransactions', 'disputes'}
_FULFILLMENT_OPERATIONS = {'orders', 'fulfillments'}
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
    """Validate the Klaviyo events page grain (JSON:API, one page per row)."""
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
        source = response_pages[generation]
        included = payload.get('included', [])
        if not isinstance(included, list):
            raise ValueError('Klaviyo response page included collection is invalid')
        profiles = set()
        for item in included:
            if not isinstance(item, dict) or item.get('type') != 'profile':
                raise ValueError('Klaviyo response page contains an unexpected included resource')
            if not isinstance(item.get('id'), str) or not item['id']:
                raise ValueError('Klaviyo included profile is missing its identity')
            profiles.add(item['id'])
        for event in payload['data']:
            if not isinstance(event, dict) or not isinstance(event.get('id'), str) or not event['id']:
                raise ValueError('Klaviyo response page contains an unidentified event')
            relationships = event.get('relationships')
            if not isinstance(relationships, dict):
                raise ValueError('Klaviyo response page event is missing its relationships')
            metric = relationships.get('metric')
            metric_data = metric.get('data') if isinstance(metric, dict) else None
            if not isinstance(metric_data, dict) or metric_data.get('id') != source['operation']:
                raise ValueError('Klaviyo response page contains an event outside its filtered metric')
            profile = relationships.get('profile')
            profile_data = profile.get('data') if isinstance(profile, dict) else None
            if (isinstance(profile_data, dict) and profile_data.get('id') is not None
                    and profile_data.get('id') not in profiles):
                raise ValueError('Klaviyo event profile relationship is missing from included profiles')



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
    if stream not in ('orders', 'order_refunds', 'returns', 'customers', 'products', 'variants',
                      'tender_transactions', 'balance_transactions', 'disputes', 'fulfillments',
                      'inventory_items', 'inventory_levels', 'events', 'acceptance'):
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
    if stream in ('customers', 'products', 'variants'):
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
    if stream == 'fulfillments':
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
    if stream in ('inventory_items', 'inventory_levels'):
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
        event_rows = []
        for row in records:
            if not isinstance(row, dict) or set(row) != set(raw):
                raise ValueError('Raw row must match envelope columns')
            if row['file_id'] not in file_ids or row['payload'] != row['record_text']:
                raise ValueError('Raw row must preserve its referenced file and original JSON')
            event_rows.append(row)
        _validate_klaviyo_events_page_publication(event_rows, files, stream)
        records = event_rows
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
