"""Exhaustively replay v2 before publishing original Bulk records and HTTP pages."""
from datetime import datetime
from io import BytesIO
from .refund_capture import CaptureError, digest, encoded
from .refund_capture_v2 import RefundCaptureV2
from .raw_records import ExtractionIdentity, iter_raw_records


def prepare_refund_raw_v2(*, bucket, domain, api_version, shop_gid, extraction_id,
                          query_source, search_filter, ingested_at, page_size=50):
    capture = RefundCaptureV2(bucket=bucket, domain=domain, token="", api_version=api_version,
        shop_gid=shop_gid, extraction_id=extraction_id, query_source=query_source,
        search_filter=search_filter, page_size=page_size, read_only=True)
    seal = capture.collect()
    blob = bucket.get_blob(capture.prefix + "/complete.json")
    content = blob.download_as_bytes(if_generation_match=int(blob.generation))
    files = [seal["bulk"], *[dict(p, role="response_page") for p in seal["pages"]],
        dict(uri=f"gs://{bucket.name}/{blob.name}", generation=str(blob.generation),
             sha256=digest(content), role="completion_seal")]
    if len({f["generation"] for f in files}) != len(files):
        raise CaptureError("File generation collision")
    request_sha = digest(encoded(seal["binding"]))
    def records():
        for ref in files[:-1]:
            stored = bucket.get_blob(ref["uri"].removeprefix(f"gs://{bucket.name}/"))
            if stored is None or str(stored.generation) != ref["generation"]:
                raise CaptureError("Saved file generation changed")
            body = stored.download_as_bytes(if_generation_match=int(ref["generation"]))
            if digest(body) != ref["sha256"]:
                raise CaptureError("Saved file checksum changed")
            identity = ExtractionIdentity(shop_gid, extraction_id, ref["generation"],
                seal["binding"]["query_sha256"], request_sha, api_version, ingested_at)
            if ref["role"] == "bulk_headers":
                yield from iter_raw_records(BytesIO(body), identity)
            else:
                # HTTP bodies may be pretty-printed; they are a single raw observation.
                text = body.decode("utf-8")
                yield dict(shop_key=shop_gid, extraction_id=extraction_id, file_id=ref["generation"],
                    record_index=1, query_sha256=identity.query_sha256, request_sha256=request_sha,
                    api_version=api_version, ingested_at=ingested_at.isoformat(),
                    record_sha256=ref["sha256"], record_text=text, payload=text,
                    object_gid=None, parent_gid=None)
    times = [datetime.fromisoformat(f["captured_at"]) for f in files[:-1]]
    return dict(records=records(), files=files, counts=seal["counts"],
        raw_record_count=int(seal["bulk"]["record_count"])+len(seal["pages"]),
        query_sha256=seal["binding"]["query_sha256"], request_sha256=request_sha,
        started_at=datetime.fromisoformat(seal["bulk"]["started_at"]), completed_at=max(times))
