"""Turn a sealed Klaviyo events capture into the contract-flattened raw stream.

This adapter is read-only: it replays the capture seal and exact GCS bodies but
does not publish, mutate GCS, or call Klaviyo.  Klaviyo is account-scoped, so
shop_key is the configured account_key and the account itself is pinned by
api_key_sha256 in the capture binding.

The raw ``events`` stream is EVENT grain, flattened at the pipeline into typed
columns by the executable contract (klaviyo_events_v1.yaml) — the same pattern
as the Shopify entity contracts. One raw row per unique provider event, merged
on (shop_key, event_gid) by the publication: events are immutable, so
re-captures of overlapping windows never accumulate duplicate rows.  The exact
HTTP response pages stay the audit surface in GCS (generation+checksum pinned;
the ingestion_runs manifest references them), and original_payload carries the
verbatim event object so flattening is lossless by construction.
"""
from datetime import datetime

from .klaviyo_capture import KlaviyoCapture
from .klaviyo_events_contract import EventContract, flatten_event
from .klaviyo_queries import compile_klaviyo_event_plans
from .refund_capture import CaptureError, decode, digest, encoded

STREAM = "events"
CONTRACT = EventContract()


def prepare_klaviyo_raw(*, bucket, token, account_key, extraction_id, metrics,
                        window_start, window_end, ingested_at, published_at,
                        page_size=200):
    if ingested_at.utcoffset() is None or published_at.utcoffset() is None:
        raise ValueError("Timezone-aware ingestion and publication timestamps required")
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
            payload = decode(body)
            context = {"shop_key": account_key, "extraction_id": extraction_id,
                       "ingested_at": ingested_at.isoformat(), "published_at": published_at.isoformat()}
            for event, profiles in capture._page_events(page["operation"], payload):
                profile_id = (event.get("relationships", {}).get("profile", {}).get("data") or {}) \
                    .get("id")
                try:
                    yield flatten_event(event, profiles.get(profile_id), context, CONTRACT)
                except CaptureError as exc:
                    # Fail-closed stays: a mismatched type aborts the extraction.
                    # The event identity makes the offending event findable
                    # without re-processing the window.
                    raise CaptureError(f"event {event['id']}: {exc}") from None

    return {
        "streams": {
            STREAM: {
                "records": records(),
                "files": files,
                "raw_record_count": sum(seal.get("counts", {}).values()),
                "counts": dict(seal.get("counts", {})),
                "query_sha256": plan_sha,
                "request_sha256": request_sha,
                "started_at": min(timestamps) if timestamps else None,
                "completed_at": max(timestamps) if timestamps else None,
            },
        },
        "counts": dict(seal.get("counts", {})),
        "raw_record_count": sum(seal.get("counts", {}).values()),
        "completion_seal": seal_file,
    }
