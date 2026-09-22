"""Physical-grain checks for a Returns Bulk publication.

Mirrors the orders bulk publication: one landed JSONL file whose raw rows
cover every line contiguously, checksums matching their text.
"""
import hashlib
import json


def validate_returns_publication_v2(rows, files):
    if not isinstance(files, list) or len(files) != 1:
        raise ValueError("Returns v2 requires exactly one bulk file")
    bulk = files[0]
    if not isinstance(bulk, dict) or not all(k in bulk for k in ("uri", "generation", "sha256")):
        raise ValueError("Returns v2 bulk file reference is incomplete")
    generation = bulk["generation"]
    indexes, expected = set(), 0
    for row in rows:
        expected += 1
        if row["file_id"] != generation:
            raise ValueError("Raw record does not reference the landed bulk file")
        text = row["record_text"]
        if row["payload"] != text or hashlib.sha256(text.encode()).hexdigest() != row["record_sha256"]:
            raise ValueError("Raw content checksum mismatch")
        payload = json.loads(text)
        if row["object_gid"] != payload.get("id"):
            raise ValueError("Raw record identity differs from payload")
        if row["record_index"] in indexes or row["record_index"] != expected:
            raise ValueError("Raw record indexes must be contiguous")
        indexes.add(row["record_index"])
    if not indexes:
        raise ValueError("Returns v2 bulk publication is empty")
