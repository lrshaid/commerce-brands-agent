"""Validated, create-only GCS landing; not a published warehouse extraction."""
from dataclasses import asdict
import hashlib
import json
import re
import tempfile

from google.api_core.exceptions import PreconditionFailed

from .raw_records import iter_raw_records


class ReplayConflict(ValueError):
    """Same logical file key was already used for a different result."""


def land_jsonl(source, bucket, identity, stream, *, max_file_bytes=256 * 1024 * 1024):
    """Validate fully, then create exactly one immutable object or verify replay.

    One file per extraction/stream in this first implementation. Caller must use a
    new extraction_id for a new provider operation. No public URLs, source payload
    logging, raw table writes, manifest publication or checkpoint changes.
    """
    if not re.fullmatch('[a-z][a-z0-9_]{0,63}', stream):
        raise ValueError('Invalid stream name')
    if max_file_bytes < 1:
        raise ValueError('max_file_bytes must be positive')
    key_parts = [identity.shop_key, stream, identity.extraction_id]
    key = hashlib.sha256(json.dumps(key_parts, separators=(',', ':')).encode()).hexdigest()
    name = f'raw/v1/{stream}/{key}.jsonl'
    binding = asdict(identity)
    # Timestamp is an observation, not replay identity. file_id comes from GCS.
    binding.pop('ingested_at')
    binding.pop('file_id')
    binding['stream'] = stream
    binding_hash = hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()
    with tempfile.SpooledTemporaryFile(max_size=4 * 1024 * 1024, mode='w+b') as staged:
        digest = hashlib.sha256()
        size = 0
        while chunk := source.read(1024 * 1024):
            size += len(chunk)
            if size > max_file_bytes:
                raise ValueError('Raw file exceeds configured size limit')
            staged.write(chunk)
            digest.update(chunk)
        staged.seek(0)
        count = sum(1 for _ in iter_raw_records(staged, identity))
        metadata = {'sha256': digest.hexdigest(), 'binding_sha256': binding_hash,
                    'record_count': str(count), 'contract_version': '1'}
        blob = bucket.blob(name)
        blob.metadata = metadata
        staged.seek(0)
        replay = False
        try:
            blob.upload_from_file(staged, rewind=True, content_type='application/x-ndjson',
                                  if_generation_match=0, checksum='auto', timeout=120)
        except PreconditionFailed:
            existing = bucket.get_blob(name)
            if existing is None or existing.metadata != metadata or existing.size != size:
                raise ReplayConflict('Existing raw object conflicts with replay identity/content') from None
            # Do not trust custom checksum metadata alone; read the pinned generation.
            staged.seek(0)
            staged.truncate()
            existing.download_to_file(staged, if_generation_match=int(existing.generation),
                                      checksum='auto', timeout=120)
            staged.seek(0)
            actual = hashlib.sha256()
            while chunk := staged.read(1024 * 1024):
                actual.update(chunk)
            if actual.hexdigest() != metadata['sha256']:
                raise ReplayConflict('Existing raw object checksum does not match')
            blob = existing
            replay = True
        if blob.generation is None:
            raise RuntimeError('Upload did not provide an immutable object generation')
        return {'uri': f'gs://{bucket.name}/{name}', 'generation': str(blob.generation),
                'file_id': str(blob.generation), 'sha256': metadata['sha256'],
                'record_count': count, 'size_bytes': size, 'replay': replay,
                'published': False}
