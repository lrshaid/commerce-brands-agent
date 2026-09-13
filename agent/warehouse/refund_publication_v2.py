"""Physical-grain checks in addition to exhaustive read-only capture replay."""
from collections import defaultdict
import hashlib
import json


def validate_refund_publication_v2(rows, files):
    by_generation = {f["generation"]: f for f in files}
    if len(by_generation) != len(files):
        raise ValueError("Duplicate file generations")
    bulk = [f for f in files if f.get("role") == "bulk_headers"]
    seals = [f for f in files if f.get("role") == "completion_seal"]
    if len(bulk) != 1 or len(seals) != 1:
        raise ValueError("V2 requires one bulk file and one seal")
    counts = defaultdict(int)
    indexes = defaultdict(set)
    for row in rows:
        ref = by_generation.get(row["file_id"], {})
        role = ref.get("role")
        text = row["record_text"]
        if row["payload"] != text or hashlib.sha256(text.encode()).hexdigest() != row["record_sha256"]:
            raise ValueError("Raw content checksum mismatch")
        if row["record_index"] in indexes[row["file_id"]]:
            raise ValueError("Duplicate raw record")
        indexes[row["file_id"]].add(row["record_index"])
        counts[row["file_id"]] += 1
        payload = json.loads(text)
        if role == "bulk_headers":
            if row["parent_gid"] is not None or payload.get("id") != row["object_gid"] or not isinstance(payload.get("refunds"), list):
                raise ValueError("Invalid bulk refund header")
        elif role == "response_page":
            if row["record_index"] != 1 or row["object_gid"] is not None or row["parent_gid"] is not None:
                raise ValueError("Invalid HTTP page grain")
            if ref["sha256"] != row["record_sha256"] or payload.get("errors") or not isinstance(payload.get("data"), dict):
                raise ValueError("Invalid GraphQL response")
            operation, variables = ref["operation"], ref["variables"]
            if operation == "refundBatch":
                nodes = payload["data"].get("nodes")
                if (not isinstance(nodes, list) or any(not isinstance(n, dict) for n in nodes)
                        or [n.get("id") for n in nodes] != variables.get("ids")
                        or any(n.get("__typename") != "Refund" for n in nodes)):
                    raise ValueError("Invalid Refund batch")
            else:
                root = "return" if operation in ("returnLineItems", "exchangeLineItems") else "refund"
                node = payload["data"].get(root)
                if not isinstance(node, dict) or node.get("id") != variables.get("id") or not isinstance(node.get(operation), dict):
                    raise ValueError("Invalid top-up owner or connection")
        else:
            raise ValueError("Raw record has no source file")
    for ref in files:
        role = ref.get("role")
        expected = int(ref["record_count"]) if role == "bulk_headers" else 1 if role == "response_page" else 0
        if role not in ("bulk_headers", "response_page", "completion_seal") or counts[ref["generation"]] != expected:
            raise ValueError("Raw file coverage mismatch")
        if indexes[ref["generation"]] != set(range(1, expected+1)):
            raise ValueError("Raw record indexes are not contiguous")
