"""Immutable HTTP page capture for the refund query plan, not warehouse publication.

Files preserve the decoded UTF-8 HTTP entity body exactly (including whitespace).
No HTTP headers/credentials are stored. A seal proves traversal, not a transactional
Shopify snapshot or business-event completeness. Partial captures cannot be sealed.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import re
import time

from google.api_core.exceptions import PreconditionFailed
import requests

from .raw_records import _object, _invalid_constant
from .refund_queries import compile_refund_queries


class CaptureError(RuntimeError):
    pass


def digest(value):
    return hashlib.sha256(value).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def decode(body):
    try:
        result = json.loads(body.decode("utf-8"), object_pairs_hook=_object,
                            parse_float=Decimal, parse_constant=_invalid_constant)
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (ValueError, UnicodeError, RecursionError):
        raise CaptureError("Invalid captured JSON response") from None


def gid(value, kind):
    if not isinstance(value, str) or not re.fullmatch(rf"gid://shopify/{kind}/[0-9]+", value):
        raise CaptureError("Invalid provider object identity")
    return value


@dataclass(frozen=True)
class Page:
    reference: dict
    body: bytes = field(repr=False)

    @property
    def data(self):
        body = decode(self.body)
        if body.get("errors") or not isinstance(body.get("data"), dict):
            raise CaptureError("Captured GraphQL response is incomplete or failed")
        return body["data"]


class RefundCapture:
    def __init__(self, *, bucket, domain, token, api_version, shop_gid, extraction_id,
                 query_source, search_filter, page_size=50, timeout_seconds=900,
                 max_pages=2000, max_bytes=256 * 1024 * 1024, read_only=False):
        self.read_only = read_only
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.myshopify\.com", domain):
            raise CaptureError("Invalid Shopify domain")
        if not re.fullmatch(r"20[0-9]{2}-(01|04|07|10)", api_version) or (not read_only and not token.strip()):
            raise CaptureError("Missing credential or API version")
        if not extraction_id or not search_filter.strip():
            raise CaptureError("Explicit extraction identity and search scope are required")
        if not 1 <= page_size <= 100 or not 1 <= timeout_seconds <= 1200:
            raise CaptureError("Invalid capture bounds")
        if not 1 <= max_pages <= 10000 or not 1 <= max_bytes <= 256 * 1024 * 1024:
            raise CaptureError("Invalid capture resource limits")
        gid(shop_gid, "Shop")  # Caller must authenticate/verify this ID before capture.
        plan = compile_refund_queries(query_source)
        self.operations = dict(zip(("orders", "refundLineItems", "transactions", "orderAdjustments"), plan.documents()))
        self.binding = {
            "format_version": 1, "domain": domain, "shop_gid": shop_gid,
            "api_version": api_version, "extraction_id": extraction_id,
            "query_sha256": digest(query_source.encode()),
            "plan_sha256": digest(encoded(self.operations)),
            "scope_sha256": digest(encoded({"query": search_filter, "first": page_size})),
        }
        key = digest(encoded([shop_gid, extraction_id]))
        self.prefix = f"pages/v1/order_refunds/{key}"
        self.bucket, self.domain, self._token = bucket, domain, token.strip()
        self.api_version, self.search_filter, self.page_size = api_version, search_filter, page_size
        self.deadline = time.monotonic() + timeout_seconds
        self.max_pages, self.max_bytes = max_pages, max_bytes
        self.pages, self.bytes = [], 0
        self._request_keys = set()
        self._finished = False
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
            old = blob.download_as_bytes(if_generation_match=int(blob.generation))
            if decode(old) != self.binding:
                raise CaptureError("Extraction identity is already bound to another plan/scope") from None

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
                    if response.status_code != 200:
                        raise CaptureError(f"Shopify HTTP page request failed ({response.status_code})")
                    if response.headers.get("X-Shopify-API-Version") != self.api_version:
                        raise CaptureError("Shopify API version mismatch")
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
        if self._finished:
            raise CaptureError("Capture is already sealed")
        if time.monotonic() >= self.deadline or len(self.pages) >= self.max_pages:
            raise CaptureError("Capture deadline or page limit reached")
        document = self.operations[operation]
        request_hash = digest(encoded({"query": document, "variables": variables}))
        if request_hash in self._request_keys:
            raise CaptureError("Duplicate page request within traversal")
        name = f"{self.prefix}/{request_hash}.json"
        existing = self.bucket.get_blob(name)
        if existing is None:
            if self.read_only:
                raise CaptureError("Missing page in read-only capture")
            body = self._http(document, variables)
            # Store even a JSON/GraphQL error response for restricted diagnostics;
            # validation below prevents using it to advance a cursor or seal.
            blob = self.bucket.blob(name)
            blob.metadata = {"response_sha256": digest(body), "request_sha256": request_hash,
                             "api_version": self.api_version,
                             "captured_at": datetime.now(timezone.utc).isoformat()}
            try:
                blob.upload_from_string(body, content_type="application/json", if_generation_match=0)
                existing = blob
            except PreconditionFailed:
                existing = self.bucket.get_blob(name)
        if existing is None or existing.generation is None or existing.size > 2 * 1024 * 1024:
            raise CaptureError("Missing or oversized captured page")
        body = existing.download_as_bytes(if_generation_match=int(existing.generation))
        metadata = existing.metadata or {}
        if (metadata.get("response_sha256") != digest(body) or metadata.get("request_sha256") != request_hash
                or metadata.get("api_version") != self.api_version):
            raise CaptureError("Captured page checksum or identity mismatch")
        try:
            if datetime.fromisoformat(metadata["captured_at"]).utcoffset() is None:
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            raise CaptureError("Captured page timestamp is missing or invalid") from None
        self.bytes += len(body)
        if self.bytes > self.max_bytes:
            raise CaptureError("Total capture size limit reached")
        reference = {"uri": f"gs://{self.bucket.name}/{name}", "generation": str(existing.generation),
                     "sha256": digest(body), "request_sha256": request_hash,
                     "operation": operation, "variables": dict(variables),
                     "captured_at": metadata.get("captured_at")}
        page = Page(reference, body)
        page.data  # Validate before acknowledging/paginating this response.
        self.pages.append(reference)
        self._request_keys.add(request_hash)
        return page

    def walk(self, operation, owner=None):
        after, cursors, identifiers = None, set(), set()
        while True:
            variables = {"first": self.page_size, "after": after}
            if operation == "orders":
                variables["query"] = self.search_filter
            else:
                variables["id"] = gid(owner, "Refund")
            data = self.fetch(operation, variables).data
            if operation == "orders":
                connection = data.get("orders")
            else:
                node = data.get("node")
                if not isinstance(node, dict) or node.get("id") != owner:
                    raise CaptureError("Refund page owner mismatch")
                connection = node.get(operation)
            if not isinstance(connection, dict):
                raise CaptureError("Missing requested connection")
            info, edges = connection.get("pageInfo"), connection.get("edges")
            if (not isinstance(info, dict) or type(info.get("hasNextPage")) is not bool
                    or not isinstance(edges, list) or len(edges) > self.page_size):
                raise CaptureError("Invalid pagination response")
            next_cursor = info.get("endCursor")
            if ((info["hasNextPage"] and not edges)
                    or (edges and (not isinstance(next_cursor, str) or not next_cursor or next_cursor in cursors))):
                raise CaptureError("Nonadvancing pagination cursor")
            for edge in edges:
                node = edge.get("node") if isinstance(edge, dict) else None
                if not isinstance(node, dict):
                    raise CaptureError("Invalid connection edge")
                identifier = node.get("id")
                if identifier is not None:
                    if not isinstance(identifier, str) or identifier in identifiers:
                        raise CaptureError("Duplicate or invalid object across pages")
                    identifiers.add(identifier)
                yield node
            if not info["hasNextPage"]:
                return
            cursors.add(next_cursor)
            after = next_cursor

    def collect(self):
        counts = {"orders": 0, "refunds": 0, "refundLineItems": 0, "transactions": 0, "orderAdjustments": 0}
        refunds_seen = set()
        for order in self.walk("orders"):
            gid(order.get("id"), "Order")
            counts["orders"] += 1
            refunds = order.get("refunds")
            if not isinstance(refunds, list):
                raise CaptureError("Missing refund list")
            for refund in refunds:
                refund_id = gid(refund.get("id") if isinstance(refund, dict) else None, "Refund")
                if refund_id in refunds_seen:
                    raise CaptureError("Refund belongs to more than one captured root")
                refunds_seen.add(refund_id)
                counts["refunds"] += 1
                for operation in ("refundLineItems", "transactions", "orderAdjustments"):
                    counts[operation] += sum(1 for _ in self.walk(operation, refund_id))
        seal = {"binding": self.binding, "status": "captured", "pages": self.pages,
                "counts": counts, "response_bytes": self.bytes,
                "consistency": "multi_request_observations_not_transactional_snapshot"}
        blob = self.bucket.blob(f"{self.prefix}/complete.json")
        content = encoded(seal)
        if self.read_only:
            existing = self.bucket.get_blob(blob.name)
            if existing is None or existing.download_as_bytes(if_generation_match=int(existing.generation)) != content:
                raise CaptureError("Missing or conflicting completion seal")
            self._finished = True
            return seal
        try:
            blob.upload_from_string(content, content_type="application/json", if_generation_match=0)
        except PreconditionFailed:
            blob.reload()
            if blob.download_as_bytes(if_generation_match=int(blob.generation)) != content:
                raise CaptureError("Conflicting completed capture") from None
        self._finished = True
        return seal
