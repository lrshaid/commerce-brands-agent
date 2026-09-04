"""Validate sealed returns pages before exposing exact-response raw rows.

No Shopify calls or GCS mutations. Publication and checkpoint changes remain the
caller's responsibility. Rows are original HTTP response pages, never flattened
or reassembled business objects.
"""
from datetime import datetime

from .returns_capture import ReturnsCapture
from .refund_capture import CaptureError, decode, digest, encoded


def prepare_returns_raw(*, bucket, domain, api_version, shop_gid, extraction_id,
                        query_source, search_filter, ingested_at, page_size=50):
    if ingested_at.utcoffset() is None:
        raise ValueError("Timezone-aware ingestion timestamp required")
    capture = ReturnsCapture(
        bucket=bucket, domain=domain, token="", api_version=api_version,
        shop_gid=shop_gid, extraction_id=extraction_id, query_source=query_source,
        search_filter=search_filter, page_size=page_size, read_only=True,
    )
    seal = capture.collect()
    seal_blob = bucket.get_blob(capture.prefix + "/complete.json")
    if seal_blob is None:
        raise CaptureError("Completion seal disappeared")
    seal_bytes = seal_blob.download_as_bytes(if_generation_match=int(seal_blob.generation))
    if seal_bytes != encoded(seal):
        raise CaptureError("Completion seal changed")
    request_sha = digest(encoded(seal["binding"]))
    files = [dict(page, role="response_page") for page in seal["pages"]]
    files.append(dict(uri=f"gs://{bucket.name}/{seal_blob.name}",
                      generation=str(seal_blob.generation), sha256=digest(seal_bytes),
                      role="completion_seal"))
    generations = [page["generation"] for page in seal["pages"]]
    if len(set(generations)) != len(generations):
        raise CaptureError("Page generations collide under the current raw physical key")
    timestamps = [datetime.fromisoformat(page["captured_at"]) for page in seal["pages"]]

    def records():
        for page in seal["pages"]:
            name = page["uri"].removeprefix(f"gs://{bucket.name}/")
            blob = bucket.get_blob(name)
            if blob is None or str(blob.generation) != page["generation"]:
                raise CaptureError("Captured page generation changed")
            body = blob.download_as_bytes(if_generation_match=int(page["generation"]))
            if digest(body) != page["sha256"]:
                raise CaptureError("Captured page checksum changed")
            decode(body)
            text = body.decode("utf-8")
            yield dict(
                shop_key=shop_gid, extraction_id=extraction_id,
                file_id=page["generation"], record_index=1,
                query_sha256=seal["binding"]["query_sha256"], request_sha256=request_sha,
                api_version=api_version, ingested_at=ingested_at.isoformat(),
                record_sha256=page["sha256"], record_text=text, payload=text,
                object_gid=None, parent_gid=None,
            )

    return dict(records=records(), files=files, counts=seal["counts"],
                raw_record_count=len(seal["pages"]), query_sha256=seal["binding"]["query_sha256"],
                request_sha256=request_sha, started_at=min(timestamps),
                completed_at=max(timestamps))
