"""Bounded Bulk polling/download and complete orders-file validation.

Only COMPLETED exports can cross this boundary. Partial files are never accepted.
Download sessions have no Shopify token and no inherited netrc authentication.
"""
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
import json
import re
import tempfile
import time
from urllib.parse import urlsplit

import requests

from .raw_records import iter_raw_records
from .shopify_bulk import BulkError, _operation_id


def _count(value):
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)):
        raise BulkError("Missing or invalid provider count")
    return int(value)


def _timestamp(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.utcoffset() is None:
            raise ValueError()
        return result
    except (ValueError, TypeError, AttributeError):
        raise BulkError("Missing or invalid provider timestamp") from None


@dataclass(frozen=True)
class CompletedExport:
    operation_id: str
    object_count: int
    root_count: int
    file_size: int | None
    created_at: datetime
    completed_at: datetime
    url: str | None = field(repr=False)

    @classmethod
    def from_status(cls, status):
        if status.get("status") != "COMPLETED" or status.get("errorCode") or status.get("partialDataUrl"):
            raise BulkError("Export is not a complete successful result")
        objects, roots = _count(status.get("objectCount")), _count(status.get("rootObjectCount"))
        size = _count(status["fileSize"]) if status.get("fileSize") is not None else None
        created, completed = _timestamp(status.get("createdAt")), _timestamp(status.get("completedAt"))
        url = status.get("url")
        if roots > objects or completed < created or (objects and not url):
            raise BulkError("Inconsistent completed-export metadata")
        if not url and size not in (None, 0):
            raise BulkError("Missing nonempty export file")
        return cls(_operation_id(status.get("id")), objects, roots, size, created, completed, url)


def wait_for_export(client, operation_id, *, timeout_seconds=900, poll_seconds=5,
                    monotonic=time.monotonic, sleep=time.sleep):
    """Resume polling an exact ID; deadline does not cancel or resubmit the export."""
    if not 1 <= timeout_seconds <= 1200 or not 1 <= poll_seconds <= 30:
        raise BulkError("Invalid polling bounds")
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        status = client.status(operation_id)
        state = status.get("status")
        if state == "COMPLETED":
            return CompletedExport.from_status(status)
        if state not in ("CREATED", "RUNNING"):
            raise BulkError("Bulk export stopped without a complete result")
        remaining = deadline - monotonic()
        if remaining > 0:
            sleep(min(poll_seconds, remaining))
    raise BulkError("Bulk polling deadline reached; resume the recorded operation ID")


def _download_url(url):
    try:
        parts = urlsplit(url)
        allowed = (parts.scheme == "https" and parts.hostname == "storage.googleapis.com"
                   and parts.port in (None, 443) and not parts.username and not parts.password
                   and not parts.fragment and len(parts.path.strip("/").split("/")) >= 2)
    except (ValueError, TypeError):
        allowed = False
    if not allowed:
        raise BulkError("Export download host or URL is not allowed")


@contextmanager
def download_export(export, *, max_file_bytes=256 * 1024 * 1024, timeout_seconds=300):
    """Yield a rewindable exact file, without credentials, redirects or URL logs.

    Allowlist follows the documented storage.googleapis.com download endpoint.
    A changed provider endpoint fails closed and requires explicit review.
    """
    if not 1 <= max_file_bytes <= 256 * 1024 * 1024 or not 1 <= timeout_seconds <= 600:
        raise BulkError("Invalid download bounds")
    if export.file_size is not None and export.file_size > max_file_bytes:
        raise BulkError("Export exceeds configured file limit")
    with tempfile.SpooledTemporaryFile(max_size=4 * 1024 * 1024, mode="w+b") as output:
        if export.url:
            _download_url(export.url)
            deadline = time.monotonic() + timeout_seconds
            try:
                with requests.Session() as session:
                    session.trust_env = False
                    with session.get(export.url, stream=True, allow_redirects=False,
                                     headers={"Accept-Encoding": "identity"}, timeout=(10, 30)) as response:
                        if response.status_code != 200:
                            raise BulkError("Export download HTTP failure")
                        if response.headers.get("Content-Encoding", "identity") != "identity":
                            raise BulkError("Unexpected export encoding")
                        size = 0
                        for chunk in response.iter_content(chunk_size=1024 * 1024):
                            if time.monotonic() >= deadline:
                                raise BulkError("Export download deadline reached")
                            size += len(chunk)
                            if size > max_file_bytes:
                                raise BulkError("Export exceeds configured file limit")
                            output.write(chunk)
            except BulkError:
                raise
            except Exception:
                raise BulkError("Export download transport failure") from None
        if export.file_size is not None and output.tell() != export.file_size:
            raise BulkError("Export file size does not match provider metadata")
        output.seek(0)
        yield output


def validate_orders_file(source, identity, export):
    """Match provider totals and explicit parent IDs, independent of row order.

    Anonymous discount application records keep their physical record identity;
    no generated business ID or adjacency-based parent assignment is introduced.
    """
    roots, children, seen = set(), set(), set()
    count = 0
    source.seek(0)
    for row in iter_raw_records(source, identity):
        count += 1
        gid, parent = row["object_gid"], row["parent_gid"]
        if gid:
            if gid in seen:
                raise BulkError("Duplicate object identity within export")
            seen.add(gid)
        if parent is None:
            if not isinstance(gid, str) or not re.fullmatch(r"gid://shopify/Order/[0-9]+", gid):
                raise BulkError("Unexpected root object in orders export")
            roots.add(gid)
        else:
            if not re.fullmatch(r"gid://shopify/Order/[0-9]+", parent):
                raise BulkError("Unexpected parent type in orders export")
            children.add(parent)
            if gid is not None:
                if not re.fullmatch(r"gid://shopify/(LineItem|ShippingLine)/[0-9]+", gid):
                    raise BulkError("Unexpected identified child in orders export")
            else:
                payload = json.loads(row["record_text"])
                if not {"allocationMethod", "targetSelection", "targetType"}.issubset(payload):
                    raise BulkError("Unrecognized anonymous child in orders export")
    if children - roots:
        raise BulkError("Export contains orphan child records")
    if count != export.object_count or len(roots) != export.root_count:
        raise BulkError("Export record/root counts do not match provider metadata")
    source.seek(0)
    return {"record_count": count, "root_count": len(roots)}
