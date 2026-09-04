"""Read-only capture verification; emit counts, never response payloads."""
import argparse
from collections import Counter
import hashlib
import json
import os

from google.cloud import storage
from google.oauth2.credentials import Credentials


def verify(bucket, prefix):
    seal_blob = bucket.get_blob(prefix + "/complete.json")
    if seal_blob is None:
        raise RuntimeError("No completion seal")
    seal = json.loads(seal_blob.download_as_bytes(if_generation_match=int(seal_blob.generation)))
    if seal["status"] != "captured":
        raise RuntimeError("Capture is not complete")
    counts = dict.fromkeys(("orders", "refunds", "refundLineItems", "transactions", "orderAdjustments"), 0)
    chains, refunds, requests_seen = {}, set(), set()
    page_counts, edge_counts = Counter(), {}
    total_bytes = 0
    for page in seal["pages"]:
        base = f"gs://{bucket.name}/{prefix}/"
        if not page["uri"].startswith(base) or page["request_sha256"] in requests_seen:
            raise RuntimeError("Invalid or duplicate page reference")
        requests_seen.add(page["request_sha256"])
        blob = bucket.blob(page["uri"][len(f"gs://{bucket.name}/"):], generation=int(page["generation"]))
        body = blob.download_as_bytes(if_generation_match=int(page["generation"]))
        if hashlib.sha256(body).hexdigest() != page["sha256"]:
            raise RuntimeError("Page checksum mismatch")
        response = json.loads(body)
        if response.get("errors"):
            raise RuntimeError("Sealed page has GraphQL errors")
        operation, variables = page["operation"], page["variables"]
        owner = variables.get("id")
        key = (operation, owner)
        expected = chains.get(key, (None, False))
        if expected[1] or variables.get("after") != expected[0] or variables["first"] != 50:
            raise RuntimeError("Page chain or page size mismatch")
        data = response["data"]
        if operation != "orders" and data["node"]["id"] != owner:
            raise RuntimeError("Wrong refund owner")
        connection = data["orders"] if operation == "orders" else data["node"][operation]
        info, edges = connection["pageInfo"], connection["edges"]
        if type(info["hasNextPage"]) is not bool:
            raise RuntimeError("Invalid terminal flag")
        chains[key] = (info["endCursor"], not info["hasNextPage"])
        counts[operation] += len(edges)
        page_counts[operation] += 1
        edge_counts.setdefault(operation, []).append(len(edges))
        if operation == "orders":
            for edge in edges:
                for refund in edge["node"]["refunds"]:
                    if refund["id"] in refunds:
                        raise RuntimeError("Repeated refund")
                    refunds.add(refund["id"])
        total_bytes += len(body)
    expected_chains = {("orders", None)} | {
        (op, owner) for owner in refunds for op in ("refundLineItems", "transactions", "orderAdjustments")}
    counts["refunds"] = len(refunds)
    if (set(chains) != expected_chains or not all(end for _, end in chains.values())
            or counts != seal["counts"] or total_bytes != seal["response_bytes"]):
        raise RuntimeError("Incomplete capture or count mismatch")
    return dict(verified=True, counts=counts, pages=dict(page_counts), page_lengths=edge_counts,
                response_bytes=total_bytes, seal_generation=str(seal_blob.generation), warehouse_published=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prefix", required=True)
    args = parser.parse_args()
    token = os.environ.get("GOOGLE_OAUTH_ACCESS_TOKEN")
    client = storage.Client(project="commerce-agents-dev", credentials=Credentials(token) if token else None)
    print(json.dumps(verify(client.bucket("commerce-agents-dev-landing"), args.prefix), indent=2))
