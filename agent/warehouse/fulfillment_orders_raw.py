"""Replay a sealed fulfillment-orders capture into two exact-page streams."""
from datetime import datetime

from .fulfillment_orders_capture import FulfillmentOrdersCapture
from .refund_capture import CaptureError, decode, digest, encoded

_STREAM_OPERATIONS = {"fulfillment_orders": ("fulfillmentOrders",),
                      "fulfillment_order_line_items": ("lineItems",)}


def prepare_fulfillment_orders_raw(*, bucket, domain, api_version, shop_gid,
                                   extraction_id, query_source, window_start,
                                   window_end, ingested_at, page_size=50):
    if ingested_at.utcoffset() is None:
        raise ValueError("Timezone-aware ingestion timestamp required")
    capture = FulfillmentOrdersCapture(
        bucket=bucket, domain=domain, token="", api_version=api_version,
        shop_gid=shop_gid, extraction_id=extraction_id, query_source=query_source,
        window_start=window_start, window_end=window_end, page_size=page_size,
        read_only=True)
    seal = capture.collect()
    seal_blob = bucket.get_blob(capture.prefix + "/complete.json")
    if seal_blob is None:
        raise CaptureError("Completion seal disappeared")
    seal_bytes = seal_blob.download_as_bytes(if_generation_match=int(seal_blob.generation))
    if seal_bytes != encoded(seal):
        raise CaptureError("Completion seal changed")
    pages = list(seal["pages"])
    generations = [page["generation"] for page in pages]
    if len(set(generations)) != len(generations):
        raise CaptureError("Page generations collide under the raw physical key")
    seal_file = dict(uri=f"gs://{bucket.name}/{seal_blob.name}",
                     generation=str(seal_blob.generation), sha256=digest(seal_bytes),
                     role="completion_seal", fulfillment_order_counts=seal["counts"])
    request_sha = digest(encoded(seal["binding"]))

    def result(stream):
        operations = _STREAM_OPERATIONS[stream]
        selected = [page for page in pages if page["operation"] in operations]
        files = [dict(page, role="response_page") for page in selected] + [seal_file]
        timestamps = [datetime.fromisoformat(page["captured_at"]) for page in selected]
        if not timestamps:
            timestamps = [ingested_at]

        def records():
            for page in selected:
                name = page["uri"].removeprefix(f"gs://{bucket.name}/")
                blob = bucket.get_blob(name)
                if blob is None or str(blob.generation) != page["generation"]:
                    raise CaptureError("Captured fulfillment-orders page generation changed")
                body = blob.download_as_bytes(if_generation_match=int(page["generation"]))
                if digest(body) != page["sha256"]:
                    raise CaptureError("Captured fulfillment-orders page checksum changed")
                decode(body)
                text = body.decode("utf-8")
                yield dict(shop_key=shop_gid, extraction_id=extraction_id,
                           file_id=page["generation"], record_index=1,
                           query_sha256=seal["binding"]["query_sha256"],
                           request_sha256=request_sha, api_version=api_version,
                           ingested_at=ingested_at.isoformat(), record_sha256=page["sha256"],
                           record_text=text, payload=text, object_gid=None, parent_gid=None)
        return {"records": records(), "files": files, "raw_record_count": len(selected),
                "counts": seal["counts"], "query_sha256": seal["binding"]["query_sha256"],
                "request_sha256": request_sha, "started_at": min(timestamps),
                "completed_at": max(timestamps)}

    return {"streams": {stream: result(stream) for stream in _STREAM_OPERATIONS},
            "counts": seal["counts"], "raw_record_count": len(pages)}
