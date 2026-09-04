"""Pinned, independently paginated capture for Shopify returns.

This module only captures exact GraphQL response pages and a completion seal.  It
does not allocate money, classify business outcomes, or publish warehouse rows.
"""
from datetime import datetime, timezone
import re
import time
import requests
from google.api_core.exceptions import PreconditionFailed

from .refund_capture import CaptureError, decode, digest, encoded, gid
from .returns_queries import compile_return_queries


class ReturnsCapture:
    def __init__(self, *, bucket, domain, token, api_version, shop_gid, extraction_id,
                 query_source, search_filter, page_size=50, timeout_seconds=900,
                 max_pages=2000, max_bytes=256 * 1024 * 1024, read_only=False):
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.myshopify\.com", domain):
            raise CaptureError("Invalid Shopify domain")
        if not re.fullmatch(r"20[0-9]{2}-(01|04|07|10)", api_version) or (not read_only and not token.strip()):
            raise CaptureError("Missing credential or API version")
        if not extraction_id or not search_filter.strip() or not 1 <= page_size <= 100:
            raise CaptureError("Explicit extraction identity, scope, and bounds are required")
        if not 1 <= timeout_seconds <= 1200 or not 1 <= max_pages <= 10000:
            raise CaptureError("Invalid capture bounds")
        if not 1 <= max_bytes <= 256 * 1024 * 1024:
            raise CaptureError("Invalid capture resource limit")
        gid(shop_gid, "Shop")
        self.read_only = read_only
        self.bucket, self.domain, self._token = bucket, domain, token.strip()
        self.operations = dict(zip(("orders", "returns", "returnLineItems", "refunds"),
                                   compile_return_queries(query_source).documents()))
        self.binding = {"format_version": 1, "stream": "returns", "domain": domain,
                        "shop_gid": shop_gid, "api_version": api_version,
                        "extraction_id": extraction_id,
                        "query_sha256": digest(query_source.encode()),
                        "plan_sha256": digest(encoded(self.operations)),
                        "scope_sha256": digest(encoded({"query": search_filter, "first": page_size}))}
        key = digest(encoded([shop_gid, extraction_id, "returns"]))
        self.prefix = f"pages/v1/returns/{key}"
        self.api_version, self.search_filter, self.page_size = api_version, search_filter, page_size
        self.deadline, self.max_pages, self.max_bytes = time.monotonic() + timeout_seconds, max_pages, max_bytes
        self.pages, self.bytes, self._request_keys, self._finished = [], 0, set(), False
        self._bind()

    def _bind(self):
        blob = self.bucket.blob(f"{self.prefix}/intent.json")
        if self.read_only:
            existing = self.bucket.get_blob(blob.name)
            if existing is None or decode(existing.download_as_bytes(if_generation_match=int(existing.generation))) != self.binding:
                raise CaptureError("Missing or conflicting read-only capture binding")
            return
        try:
            blob.upload_from_string(encoded(self.binding), content_type="application/json", if_generation_match=0)
        except PreconditionFailed:
            blob.reload()
            if decode(blob.download_as_bytes(if_generation_match=int(blob.generation))) != self.binding:
                raise CaptureError("Extraction identity is already bound to another returns plan")

    def _http(self, document, variables):
        if self.read_only:
            raise CaptureError("Read-only capture cannot call Shopify")
        try:
            with requests.Session() as session:
                session.trust_env = False
                with session.post(f"https://{self.domain}/admin/api/{self.api_version}/graphql.json",
                                  json={"query": document, "variables": variables},
                                  headers={"X-Shopify-Access-Token": self._token},
                                  stream=True, allow_redirects=False, timeout=(10, 30)) as response:
                    if response.status_code != 200 or response.headers.get("X-Shopify-API-Version") != self.api_version:
                        raise CaptureError("Shopify page request failed or API version mismatched")
                    body = bytearray()
                    for chunk in response.iter_content(chunk_size=65536):
                        if time.monotonic() >= self.deadline:
                            raise CaptureError("Capture deadline reached")
                        body.extend(chunk)
                        if len(body) > 2 * 1024 * 1024:
                            raise CaptureError("HTTP response exceeds capture limit")
                    return bytes(body)
        except CaptureError:
            raise
        except Exception:
            raise CaptureError("Shopify page transport failed; response details suppressed") from None

    def fetch(self, operation, variables):
        if self._finished or time.monotonic() >= self.deadline or len(self.pages) >= self.max_pages:
            raise CaptureError("Capture deadline, page limit, or sealed capture reached")
        request_hash = digest(encoded({"query": self.operations[operation], "variables": variables}))
        if request_hash in self._request_keys:
            raise CaptureError("Duplicate page request within traversal")
        name = f"{self.prefix}/{request_hash}.json"
        existing = self.bucket.get_blob(name)
        if existing is None:
            if self.read_only:
                raise CaptureError("Missing page in read-only capture")
            body = self._http(self.operations[operation], variables)
            blob = self.bucket.blob(name)
            blob.metadata = {"response_sha256": digest(body), "request_sha256": request_hash,
                             "api_version": self.api_version, "captured_at": datetime.now(timezone.utc).isoformat()}
            try:
                blob.upload_from_string(body, content_type="application/json", if_generation_match=0)
                existing = blob
            except PreconditionFailed:
                existing = self.bucket.get_blob(name)
        if existing is None or existing.generation is None or existing.size > 2 * 1024 * 1024:
            raise CaptureError("Missing or oversized captured page")
        body = existing.download_as_bytes(if_generation_match=int(existing.generation))
        metadata = existing.metadata or {}
        if metadata.get("response_sha256") != digest(body) or metadata.get("request_sha256") != request_hash:
            raise CaptureError("Captured page checksum or identity mismatch")
        try:
            if datetime.fromisoformat(metadata["captured_at"]).utcoffset() is None:
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            raise CaptureError("Captured page timestamp is missing or invalid") from None
        self.bytes += len(body)
        if self.bytes > self.max_bytes:
            raise CaptureError("Total capture size limit reached")
        data = decode(body)
        if data.get("errors") or not isinstance(data.get("data"), dict):
            raise CaptureError("Captured GraphQL response is incomplete or failed")
        reference = {"uri": f"gs://{self.bucket.name}/{name}", "generation": str(existing.generation),
                     "sha256": digest(body), "request_sha256": request_hash, "operation": operation,
                     "variables": dict(variables), "captured_at": metadata["captured_at"]}
        self.pages.append(reference)
        self._request_keys.add(request_hash)
        return data["data"]

    def walk(self, operation, owner=None):
        after, cursors, identifiers = None, set(), set()
        while True:
            variables = {"first": self.page_size, "after": after}
            if operation == "orders":
                variables["query"] = self.search_filter
            else:
                variables["id"] = gid(owner, "Order" if operation == "returns" else "Return")
            data = self.fetch(operation, variables)
            if operation == "orders":
                connection = data.get("orders")
            else:
                node = data.get("node")
                expected = "Order" if operation == "returns" else "Return"
                if not isinstance(node, dict) or node.get("id") != owner:
                    raise CaptureError(f"{expected} page owner mismatch")
                connection = node.get(operation)
            if not isinstance(connection, dict):
                raise CaptureError("Missing requested connection")
            info, edges = connection.get("pageInfo"), connection.get("edges")
            if not isinstance(info, dict) or type(info.get("hasNextPage")) is not bool or not isinstance(edges, list):
                raise CaptureError("Invalid pagination response")
            next_cursor = info.get("endCursor")
            if info["hasNextPage"] and not edges:
                raise CaptureError("Nonadvancing pagination cursor")
            if info["hasNextPage"] and (not isinstance(next_cursor, str) or not next_cursor or next_cursor in cursors):
                raise CaptureError("Nonadvancing pagination cursor")
            for edge in edges:
                item = edge.get("node") if isinstance(edge, dict) else None
                if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                    raise CaptureError("Invalid connection edge identity")
                if item["id"] in identifiers:
                    raise CaptureError("Duplicate object across pages")
                identifiers.add(item["id"])
                yield item
            if not info["hasNextPage"]:
                return
            cursors.add(next_cursor)
            after = next_cursor

    def collect(self):
        counts = {k: 0 for k in ("orders", "returns", "returnLineItems", "refunds")}
        # A Return may be linked to many things downstream, but it must have one
        # owning Order within this extraction.  ``walk`` deliberately scopes its
        # duplicate detector to one connection, so enforce this cross-order
        # invariant here before traversing children or writing the completion seal.
        return_owners = {}
        for order in self.walk("orders"):
            order_id = gid(order.get("id"), "Order")
            counts["orders"] += 1
            for item in self.walk("returns", order_id):
                return_id = gid(item.get("id"), "Return")
                previous_owner = return_owners.setdefault(return_id, order_id)
                if previous_owner != order_id:
                    raise CaptureError("Return is linked to multiple orders in one extraction")
                counts["returns"] += 1
                counts["returnLineItems"] += sum(1 for _ in self.walk("returnLineItems", return_id))
                counts["refunds"] += sum(1 for _ in self.walk("refunds", return_id))
        seal = {"binding": self.binding, "status": "captured", "pages": self.pages,
                "counts": counts, "response_bytes": self.bytes,
                "consistency": "multi_request_observations_not_transactional_snapshot"}
        content = encoded(seal)
        blob = self.bucket.blob(f"{self.prefix}/complete.json")
        if self.read_only:
            existing = self.bucket.get_blob(blob.name)
            if existing is None or existing.download_as_bytes(if_generation_match=int(existing.generation)) != content:
                raise CaptureError("Missing or conflicting completion seal")
        else:
            try:
                blob.upload_from_string(content, content_type="application/json", if_generation_match=0)
            except PreconditionFailed:
                blob.reload()
                if blob.download_as_bytes(if_generation_match=int(blob.generation)) != content:
                    raise CaptureError("Conflicting completed capture")
        self._finished = True
        return seal
