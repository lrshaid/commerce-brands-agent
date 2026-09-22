"""Physical-grain checks for a Returns Bulk publication.

Mirrors refund_publication_v2 for the pure-bulk returns family: one bulk
JSONL file plus the completion seal, raw rows covering every line.
"""
import hashlib
import json


def validate_returns_publication_v2(rows, files):
    by_generation = {f["generation"]: f for f in files}
    if len(by_generation) != len(files):
        raise ValueError("Duplicate file generations")
    bulk = [f for f in files if f.get("role") == "bulk_returns"]
    seals = [f for f in files if f.get("role") == "completion_seal"]
    if len(bulk) != 1 or len(seals) != 1:
        raise ValueError("Returns v2 requires one bulk file and one seal")
    counts, indexes = {}, set()
    for row in rows:
        ref = by_generation.get(row["file_id"], {})
        if ref.get("role") != "bulk_returns":
            raise ValueError("Raw record has no bulk source file")
        text = row["record_text"]
        if row["payload"] != text or hashlib.sha256(text.encode()).hexdigest() != row["record_sha256"]:
            raise ValueError("Raw content checksum mismatch")
        if row["record_index"] in indexes:
            raise ValueError("Duplicate raw record")
        indexes.add(row["record_index"])
        counts[row["file_id"]] = counts.get(row["file_id"], 0) + 1
        payload = json.loads(text)
        if row["object_gid"] != payload.get("id"):
            raise ValueError("Raw record identity differs from payload")
    generation = bulk[0]["generation"]
    expected = int(bulk[0]["record_count"])
    if counts.get(generation) != expected or indexes != set(range(1, expected + 1)):
        raise ValueError("Raw file coverage mismatch")
