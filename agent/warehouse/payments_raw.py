"""Turn a sealed payments capture into stream-scoped raw envelopes.

Read-only: replays the capture seal and exact GCS bodies without publishing,
mutating GCS, or calling Shopify.  Tender transactions, balance transactions and
disputes each receive only their own response pages plus the same completion-seal
reference.  An empty stream keeps its seal; the publisher must explicitly accept
that case via the sealed zero count.
"""
from datetime import datetime

from .payments_capture import PaymentsCapture
from .refund_capture import CaptureError, decode, digest, encoded

_STREAM_OPERATIONS = {"tender_transactions": "tenderTransactions",
                      "balance_transactions": "balanceTransactions",
                      "disputes": "disputes"}


def prepare_payments_raw(*, bucket, domain, api_version, shop_gid, extraction_id,
                         tender_source, balance_source, disputes_source, search_filter,
                         ingested_at, page_size=50):
    if ingested_at.utcoffset() is None:
        raise ValueError("Timezone-aware ingestion timestamp required")
    capture = PaymentsCapture(
        bucket=bucket, domain=domain, token="", api_version=api_version,
        shop_gid=shop_gid, extraction_id=extraction_id, tender_source=tender_source,
        balance_source=balance_source, disputes_source=disputes_source,
        search_filter=search_filter, page_size=page_size, read_only=True,
    )
    seal = capture.collect()
    seal_blob = bucket.get_blob(capture.prefix + "/complete.json")
    if seal_blob is None:
        raise CaptureError("Completion seal disappeared")
    seal_bytes = seal_blob.download_as_bytes(if_generation_match=int(seal_blob.generation))
    if seal_bytes != encoded(seal):
        raise CaptureError("Completion seal changed")

    pages = list(seal.get("pages", []))
    generations = [str(page.get("generation")) for page in pages]
    if any(not generation.isdigit() for generation in generations):
        raise CaptureError("Payments page generation is invalid")
    for operation in _STREAM_OPERATIONS.values():
        scoped = [str(page.get("generation")) for page in pages
                  if page.get("operation") == operation]
        if len(set(scoped)) != len(scoped):
            raise CaptureError("Page generations collide within a payments stream")
    timestamps = [datetime.fromisoformat(page["captured_at"]) for page in pages]
    request_sha = digest(encoded(seal["binding"]))
    seal_file = dict(
        uri=f"gs://{bucket.name}/{seal_blob.name}",
        generation=str(seal_blob.generation),
        sha256=digest(seal_bytes),
        role="completion_seal",
        payments_counts=dict(seal.get("counts", {})),
    )

    def stream_result(operation):
        selected = [page for page in pages if page.get("operation") == operation]
        files = [dict(page, role="response_page") for page in selected]
        files.append(dict(seal_file))

        def records():
            for page in selected:
                name = page["uri"].removeprefix(f"gs://{bucket.name}/")
                blob = bucket.get_blob(name)
                if blob is None or str(blob.generation) != str(page["generation"]):
                    raise CaptureError("Captured payments page generation changed")
                body = blob.download_as_bytes(if_generation_match=int(page["generation"]))
                if digest(body) != page["sha256"]:
                    raise CaptureError("Captured payments page checksum changed")
                decode(body)
                text = body.decode("utf-8")
                yield dict(
                    shop_key=shop_gid, extraction_id=extraction_id,
                    file_id=str(page["generation"]), record_index=1,
                    query_sha256=seal["binding"]["query_sha256"][operation],
                    request_sha256=request_sha, api_version=api_version,
                    ingested_at=ingested_at.isoformat(), record_sha256=page["sha256"],
                    record_text=text, payload=text, object_gid=None, parent_gid=None,
                )

        return {
            "records": records(),
            "files": files,
            "raw_record_count": len(selected),
            "counts": {operation: seal.get("counts", {}).get(operation, 0)},
            "query_sha256": seal["binding"]["query_sha256"][operation],
            "request_sha256": request_sha,
            "started_at": min(timestamps),
            "completed_at": max(timestamps),
        }

    return {
        "streams": {stream: stream_result(operation)
                    for stream, operation in _STREAM_OPERATIONS.items()},
        "counts": dict(seal.get("counts", {})),
        "raw_record_count": len(pages),
        "completion_seal": seal_file,
    }
