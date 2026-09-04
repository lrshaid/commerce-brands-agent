"""Explicit GCS acceptance: writes one small synthetic object, no publication."""
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import sys

from google.cloud import storage
from google.oauth2.credentials import Credentials

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from agent.warehouse.raw_landing import ReplayConflict, land_jsonl
from agent.warehouse.raw_records import ExtractionIdentity


def main():
    token = os.environ.get('GOOGLE_OAUTH_ACCESS_TOKEN')
    client = storage.Client(project='commerce-agents-dev',
                            credentials=Credentials(token) if token else None)
    bucket = client.bucket('commerce-agents-dev-landing')
    identity = ExtractionIdentity('synthetic-acceptance-only', 'landing-probe-20260904-v1',
                                  'pending', 'a'*64, 'b'*64, '2026-04',
                                  datetime(2026, 9, 4, tzinfo=timezone.utc))
    value = b'{"id":"synthetic-probe","amount":"0.00"}\n'
    first = land_jsonl(io.BytesIO(value), bucket, identity, 'acceptance')
    replay = land_jsonl(io.BytesIO(value), bucket, identity, 'acceptance')
    assert replay['replay'] and first['generation'] == replay['generation']
    try:
        land_jsonl(io.BytesIO(b'{"id":"changed"}\n'), bucket, identity, 'acceptance')
    except ReplayConflict:
        pass
    else:
        raise AssertionError('Conflicting content was accepted')
    # Read again after the conflict to prove the original remains intact.
    final = land_jsonl(io.BytesIO(value), bucket, identity, 'acceptance')
    assert final['generation'] == first['generation']
    print(json.dumps({'result': 'PASS', 'object': final,
                      'checks': ['same-generation replay', 'conflict rejected',
                                 'original content preserved']}, indent=2))


if __name__ == '__main__':
    main()
