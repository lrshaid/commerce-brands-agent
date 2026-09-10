"""Turn a sealed Klaviyo events capture into the stream-scoped raw envelope.

This adapter is read-only: it replays the capture seal and exact GCS bodies but
does not publish, mutate GCS, or call Klaviyo.  The envelope fields reuse the
repo's existing raw contract verbatim (shop_key/extraction_id/file_id/
record_index plus the identity hashes); Klaviyo is account-scoped, so shop_key
is the configured account_key and the account itself is pinned by
api_key_sha256 in the capture binding.  One raw row per exact HTTP response
page, including per-metric sub-stream pages inside the single ``events``
stream.
"""
from datetime import datetime

from .klaviyo_capture import KlaviyoCapture
from .klaviyo_queries import compile_klaviyo_event_plans
from .refund_capture import CaptureError, decode, digest, encoded

STREAM = "events"


def prepare_klaviyo_raw(*, bucket, token, account_key, extraction_id, metrics,
                        window_start, window_end, ingested_at, page_size=200):
    if ingested_at.utcoffset() is None:
        raise ValueError("Timezone-aware ingestion timestamp required")
    plans = compile_klaviyo_event_plans(metrics, window_start, window_end, page_size)
    capture = KlaviyoCapture(
        bucket=bucket, token=token, account_key=account_key,
        extraction_id=extraction_id, metrics=metrics,
        window_start=window_start, window_end=window_end, page_size=page_size,
        read_only=True,
    )
    if capture.binding["metrics"] != [{"metric_id": plan.metric_id, "event_type": plan.event_type}
                                      for plan in plans]:
        raise CaptureError("Configured metric plan does not match the capture binding")
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
        raise CaptureError("Klaviyo page generation is invalid")
    if len(set(generations)) != len(generations):
        raise CaptureError("Page generations collide within the Klaviyo events stream")
    timestamps = [datetime.fromisoformat(page["captured_at"]) for page in pages]
    request_sha = digest(encoded(seal["binding"]))
    plan_sha = seal["binding"]["plan_sha256"]
    revision = seal["binding"]["revision"]
    seal_file = dict(
        uri=f"gs://{bucket.name}/{seal_blob.name}",
        generation=str(seal_blob.generation),
        sha256=digest(seal_bytes),
        role="completion_seal",
        klaviyo_counts=dict(seal.get("counts", {})),
    )
    files = [dict(page, role="response_page") for page in pages]
    files.append(seal_file)

    def records():
        for page in pages:
            name = page["uri"].removeprefix(f"gs://{bucket.name}/")
            blob = bucket.get_blob(name)
            if blob is None or str(blob.generation) != str(page["generation"]):
                raise CaptureError("Captured Klaviyo page generation changed")
            body = blob.download_as_bytes(if_generation_match=int(page["generation"]))
            if digest(body) != page["sha256"]:
                raise CaptureError("Captured Klaviyo page checksum changed")
            decode(body)
            text = body.decode("utf-8")
            yield dict(
                shop_key=account_key, extraction_id=extraction_id,
                file_id=str(page["generation"]), record_index=1,
                query_sha256=plan_sha, request_sha256=request_sha, api_version=revision,
                ingested_at=ingested_at.isoformat(), record_sha256=page["sha256"],
                record_text=text, payload=text, object_gid=None, parent_gid=None,
            )

    return {
        "streams": {
            STREAM: {
                "records": records(),
                "files": files,
                "raw_record_count": len(pages),
                "counts": dict(seal.get("counts", {})),
                "query_sha256": plan_sha,
                "request_sha256": request_sha,
                "started_at": min(timestamps) if timestamps else None,
                "completed_at": max(timestamps) if timestamps else None,
            },
        },
        "counts": dict(seal.get("counts", {})),
        "raw_record_count": len(pages),
        "completion_seal": seal_file,
    }
