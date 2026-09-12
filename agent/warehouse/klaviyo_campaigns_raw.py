"""Turn a sealed Klaviyo campaigns capture into the stream-scoped raw envelope.

Read-only replay of the capture seal and exact GCS bodies; no publication, no
GCS mutation, no Klaviyo calls.  The envelope fields reuse the repo's existing
raw contract verbatim (shop_key/extraction_id/file_id/record_index plus the
identity hashes); shop_key is the configured account_key and the account itself
is pinned by api_key_sha256 in the capture binding.  One raw row per exact HTTP
response page of the single campaigns snapshot chain.
"""
from datetime import datetime

from .klaviyo_campaigns_capture import KlaviyoCampaignsCapture
from .klaviyo_campaigns_queries import compile_klaviyo_campaigns_plans
from .refund_capture import CaptureError, decode, digest, encoded

STREAM = "campaigns"


def prepare_klaviyo_campaigns_raw(*, bucket, token, account_key, extraction_id,
                                  archived=None, ingested_at, page_size=100):
    if ingested_at.utcoffset() is None:
        raise ValueError("Timezone-aware ingestion timestamp required")
    plans = compile_klaviyo_campaigns_plans(archived, page_size)
    capture = KlaviyoCampaignsCapture(
        bucket=bucket, token=token, account_key=account_key,
        extraction_id=extraction_id, archived=archived, page_size=page_size,
        read_only=True,
    )
    if capture.binding["plan_sha256"] != digest(encoded({plan.operation: plan.first_params
                                                         for plan in plans})):
        raise CaptureError("Configured campaigns plan does not match the capture binding")
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
        raise CaptureError("Page generations collide within the Klaviyo campaigns stream")
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
