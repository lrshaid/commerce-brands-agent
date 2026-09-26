"""Executable Klaviyo events contract: flatten captured events into typed raw rows.

The contract YAML (warehouse/contracts/klaviyo_events_v1.yaml) declares one
column per flattened field with a source path in three scopes: ``$.x`` walks
the verbatim event object, ``$profile.x`` walks the included profile of the
page that carried the event, and ``$context.x`` reads publication context.
``$event`` yields the whole verbatim event (original_payload).  Flattening is
lossless by construction: original_payload is required, so anything the
contract does not flatten stays queryable inside it, and a new flattened
column is recoverable post-hoc from the payload without re-capture.

Type checks fail closed at flatten time: a value that does not match its
declared type aborts the preparation instead of landing a bad row.
"""
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path

from google.cloud import bigquery

from .refund_capture import CaptureError

CONTRACT = Path(__file__).resolve().parents[2] / 'warehouse/contracts/klaviyo_events_v1.yaml'

_JSON_TYPES = {'JSON'}
_STRING_TYPES = {'STRING'}
_INT_TYPES = {'INT64'}
_TIMESTAMP_TYPES = {'TIMESTAMP'}
_VALID_TYPES = _JSON_TYPES | _STRING_TYPES | _INT_TYPES | _TIMESTAMP_TYPES


class EventContract:
    def __init__(self, path=CONTRACT):
        import yaml
        document = yaml.safe_load(Path(path).read_text())
        self.version = document['version']
        entity = document['entity']
        self.name = entity['name']
        self.key = tuple(entity['key'])
        self.partition_by = entity['partition_by']
        self.cluster_by = tuple(entity['cluster_by'])
        self.publication = document['publication']
        self.columns = tuple(_Column(name, spec) for name, spec in entity['columns'].items())

    def bigquery_fields(self):
        return [column.bigquery_field() for column in self.columns]

    def stage_fields(self):
        """Stage table loads every value as STRING; the MERGE casts explicitly."""
        return [bigquery.SchemaField(column.name, 'STRING', mode='NULLABLE')
                for column in self.columns]


class _Column:
    def __init__(self, name, spec):
        if name != name.lower() or not name.isidentifier():
            raise CaptureError(f'Invalid event contract column name: {name}')
        self.name = name
        self.type = spec['type']
        self.required = bool(spec.get('required'))
        self.source = spec['source']
        if self.type not in _VALID_TYPES:
            raise CaptureError(f'Invalid event contract type for {name}: {self.type}')
        if self.required and self.type not in ('STRING', 'INT64', 'TIMESTAMP', 'JSON'):
            raise CaptureError(f'Invalid required type for {name}: {self.type}')

    @property
    def scope(self):
        if self.source.startswith('$profile.'):
            return 'profile', self.source[len('$profile.'):].split('.')
        if self.source.startswith('$context.'):
            return 'context', self.source[len('$context.'):].split('.')
        if self.source == '$event':
            return 'event', []
        if self.source.startswith('$.'):
            return 'event', self.source[2:].split('.')
        raise CaptureError(f'Invalid event contract source for {self.name}: {self.source}')

    def bigquery_field(self):
        return bigquery.SchemaField(self.name, self.type, mode='REQUIRED' if self.required else 'NULLABLE')


def _navigate(document, path):
    value = document
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def canonical_json(value):
    """Canonical JSON text for JSON columns.

    The capture decoder parses float literals as Decimal; re-serializing the
    verbatim event must emit them back as numbers, not raise.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      default=lambda d: float(d) if isinstance(d, Decimal) else str(d))


def flatten_event(event, profile, context, contract):
    """Flatten one verbatim event into contract-ordered string values for the stage load.

    Every value returns as STRING (canonical JSON for JSON columns) because the
    stage table loads all-string and the publication MERGE casts explicitly.
    Type checks fail closed: the contract's declared type must match the
    provider value exactly.
    """
    row = {}
    for column in contract.columns:
        scope, path = column.scope
        source = {'event': event, 'profile': profile, 'context': context}[scope]
        value = _navigate(source, path)
        if value is None:
            if column.required:
                raise CaptureError(f'Flattened event is missing required column {column.name}')
            row[column.name] = None
            continue
        if column.type in _STRING_TYPES:
            if not isinstance(value, str):
                raise CaptureError(f'Column {column.name} expects a string, got {type(value).__name__}')
            row[column.name] = value
        elif column.type in _INT_TYPES:
            if not isinstance(value, int) or isinstance(value, bool):
                raise CaptureError(f'Column {column.name} expects an integer, got {type(value).__name__}')
            row[column.name] = str(value)
        elif column.type in _TIMESTAMP_TYPES:
            if not isinstance(value, str):
                raise CaptureError(f'Column {column.name} expects an ISO timestamp string')
            try:
                if datetime.fromisoformat(value).utcoffset() is None:
                    raise ValueError()
            except ValueError:
                raise CaptureError(f'Column {column.name} is not a timezone-aware timestamp') from None
            row[column.name] = value
        elif column.type in _JSON_TYPES:
            row[column.name] = canonical_json(value)
    return row


def event_gid_column(contract):
    return contract.key[1]


def event_bigquery_schema(contract, *, stage=False):
    return contract.stage_fields() if stage else contract.bigquery_fields()
