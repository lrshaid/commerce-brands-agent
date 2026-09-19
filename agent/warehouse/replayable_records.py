"""Disk-backed replay for validated raw envelopes consumed by two publishers."""
from contextlib import contextmanager
import json
from pathlib import Path
import tempfile


@contextmanager
def replayable_records(records):
    """Spool a one-shot record iterator once and expose bounded-memory replays."""
    with tempfile.TemporaryDirectory(prefix="shopify-raw-records-") as directory:
        path = Path(directory) / "records.jsonl"
        count = 0
        with path.open("w", encoding="utf-8") as target:
            for record in records:
                target.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
                count += 1

        def factory():
            with path.open(encoding="utf-8") as source:
                for line in source:
                    yield json.loads(line)

        yield factory, count
