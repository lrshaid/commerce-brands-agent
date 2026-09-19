"""Create-only GCS landing for entity Parquet files and completion manifests."""
from dataclasses import asdict
import hashlib
import io
import json
from pathlib import Path
from urllib.parse import quote

from google.api_core.exceptions import PreconditionFailed

from .raw_landing import ReplayConflict


def _download_sha256(blob):
    target = io.BytesIO()
    blob.download_to_file(target, if_generation_match=int(blob.generation),
                          checksum="auto", timeout=120)
    return hashlib.sha256(target.getvalue()).hexdigest()


def _upload_create_only(bucket, name, source, *, sha256, size, content_type, metadata):
    blob = bucket.blob(name)
    blob.metadata = dict(metadata, sha256=sha256)
    replay = False
    try:
        blob.upload_from_file(source, rewind=True, content_type=content_type,
                              if_generation_match=0, checksum="auto", timeout=120)
    except PreconditionFailed:
        existing = bucket.get_blob(name)
        if (existing is None or existing.metadata != blob.metadata
                or existing.size != size or _download_sha256(existing) != sha256):
            raise ReplayConflict("Existing entity artifact conflicts with replay") from None
        blob = existing
        replay = True
    if blob.generation is None:
        raise RuntimeError("Entity artifact upload did not return a generation")
    return {
        "uri": f"gs://{bucket.name}/{name}",
        "generation": str(blob.generation),
        "sha256": sha256,
        "size_bytes": size,
        "replay": replay,
    }


def land_entity_artifacts(bucket, artifacts, *, source_files, query_sha256,
                          request_sha256, window_start, window_end, published_at):
    """Upload exact Parquet parts, then seal their immutable manifest."""
    for value, name in ((query_sha256, "query_sha256"), (request_sha256, "request_sha256")):
        if not isinstance(value, str) or len(value) != 64:
            raise ValueError(f"Invalid {name}")
    for value, name in ((window_start, "window_start"), (window_end, "window_end"),
                        (published_at, "published_at")):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{name} must be timezone-aware")
    safe_extraction = quote(artifacts.extraction_id, safe="")
    shop_hash = hashlib.sha256(artifacts.shop_key.encode()).hexdigest()[:16]
    landed = []
    for artifact in artifacts.files:
        name = (f"entities/v{artifacts.version}/{artifact.entity}/shop={shop_hash}/"
                f"extraction_id={safe_extraction}/{Path(artifact.path).name}")
        with open(artifact.path, "rb") as source:
            uploaded = _upload_create_only(
                bucket, name, source, sha256=artifact.sha256,
                size=artifact.size_bytes, content_type="application/vnd.apache.parquet",
                metadata={
                    "contract_sha256": artifacts.contract_sha256,
                    "entity": artifact.entity,
                    "extraction_id": artifacts.extraction_id,
                    "row_count": str(artifact.row_count),
                },
            )
        landed.append(dict(asdict(artifact), **uploaded, path=None))
        landed[-1].pop("path")
        # Replay is an observation about this upload attempt, not part of the
        # immutable extraction identity. Including it would change the manifest
        # body from false to true on an otherwise exact retry.
        landed[-1].pop("replay")
    manifest = {
        "version": artifacts.version,
        "contract_sha256": artifacts.contract_sha256,
        "shop_key": artifacts.shop_key,
        "stream": artifacts.stream,
        "extraction_id": artifacts.extraction_id,
        "query_sha256": query_sha256,
        "request_sha256": request_sha256,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "published_at": published_at.isoformat(),
        "source_files": source_files,
        "entity_files": landed,
        "entity_counts": {item["entity"]: item["row_count"] for item in landed},
    }
    body = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
    manifest_sha = hashlib.sha256(body).hexdigest()
    manifest_name = (f"entities/v{artifacts.version}/manifests/{artifacts.stream}/shop={shop_hash}/"
                     f"extraction_id={safe_extraction}/manifest.json")
    manifest_ref = _upload_create_only(
        bucket, manifest_name, io.BytesIO(body), sha256=manifest_sha, size=len(body),
        content_type="application/json",
        metadata={
            "contract_sha256": artifacts.contract_sha256,
            "extraction_id": artifacts.extraction_id,
            "stream": artifacts.stream,
        },
    )
    return dict(manifest, manifest=manifest_ref)
