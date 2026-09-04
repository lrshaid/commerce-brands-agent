"""Provider-neutral JSONL envelope for the versioned raw contract.

This parser does not publish a manifest or advance a checkpoint. A consumer must
finish parsing and validate the whole extraction before publishing any rows.
Exceptions deliberately omit payloads, which can contain customer information.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import hashlib
import json
import re
from typing import BinaryIO, Iterator


class InvalidRawRecord(ValueError):
    pass


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidRawRecord('Duplicate JSON key')
        result[key] = value
    return result


def _invalid_constant(_value):
    raise InvalidRawRecord('Non-finite JSON number')


@dataclass(frozen=True)
class ExtractionIdentity:
    shop_key: str
    extraction_id: str
    file_id: str
    query_sha256: str
    request_sha256: str
    api_version: str
    ingested_at: datetime

    def __post_init__(self):
        for value in (self.shop_key, self.extraction_id, self.file_id, self.api_version):
            if not isinstance(value, str) or not value.strip():
                raise ValueError('Extraction identity fields must be nonempty strings')
        for digest in (self.query_sha256, self.request_sha256):
            if not re.fullmatch('[0-9a-f]{64}', digest):
                raise ValueError('Expected a lowercase SHA256 digest')
        if self.ingested_at.tzinfo is None or self.ingested_at.utcoffset() is None:
            raise ValueError('ingested_at must be timezone-aware')


def iter_raw_records(source: BinaryIO, identity: ExtractionIdentity,
                     max_record_bytes: int = 8 * 1024 * 1024) -> Iterator[dict]:
    """Read bounded physical lines, preserving exact UTF-8 text minus LF/CRLF.

    JSON payload remains a JSON string for a later explicit BigQuery PARSE_JSON
    boundary. Parsing here validates syntax without converting decimal values to
    binary floats. The record_text field remains the authoritative original.
    """
    if max_record_bytes < 1:
        raise ValueError('max_record_bytes must be positive')
    index = 0
    while True:
        line = source.readline(max_record_bytes + 3)
        if not line:
            break
        index += 1
        if line.endswith(b'\n'):
            line = line[:-1]
            if line.endswith(b'\r'):
                line = line[:-1]
        if len(line) > max_record_bytes:
            raise InvalidRawRecord(f'Record {index}: exceeds size limit')
        try:
            text = line.decode('utf-8', errors='strict')
            payload = json.loads(text, object_pairs_hook=_object,
                                 parse_float=Decimal, parse_int=Decimal,
                                 parse_constant=_invalid_constant)
            if not isinstance(payload, dict):
                raise InvalidRawRecord('Expected a JSON object')
            for field in ('id', '__parentId'):
                if field in payload and payload[field] is not None and not isinstance(payload[field], str):
                    raise InvalidRawRecord('Identifier must be a string or null')
        except (ValueError, UnicodeError, RecursionError):
            raise InvalidRawRecord(f'Record {index}: invalid JSON object') from None
        yield {
            'shop_key': identity.shop_key,
            'extraction_id': identity.extraction_id,
            'file_id': identity.file_id,
            'record_index': index,
            'query_sha256': identity.query_sha256,
            'request_sha256': identity.request_sha256,
            'api_version': identity.api_version,
            'ingested_at': identity.ingested_at.isoformat(),
            'record_sha256': hashlib.sha256(line).hexdigest(),
            'record_text': text,
            'payload': text,
            'object_gid': payload.get('id'),
            'parent_gid': payload.get('__parentId'),
        }
