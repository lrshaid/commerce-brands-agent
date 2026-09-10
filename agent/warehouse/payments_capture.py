"""Pinned, independently paginated capture for Shopify payments streams.

This module only captures exact GraphQL response pages and a completion seal.  It
does not reconcile money against orders, classify disputes, or publish warehouse
rows.  Balance and dispute pages live under ``shopifyPaymentsAccount``; a missing
account fails closed instead of silently producing an empty extraction.
"""
from datetime import datetime, timezone
import re
import time
import requests
from google.api_core.exceptions import PreconditionFailed

from .refund_capture import CaptureError, decode, digest, encoded, gid
from .payments_queries import compile_payments_queries


class PaymentsCapture:
    def __init__(self, *, bucket, domain, token, api_version, shop_gid, extraction_id,
                 tender_source, balance_source, disputes_source, search_filter,
                 page_size=50, timeout_seconds=900, max_pages=2000,
                 max_bytes=256 * 1024 * 1024, read_only=False):
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.myshopify\.com", domain):
            raise CaptureError("Invalid Shopify domain")
        if not re.fullmatch(r"20[0-9]{2}-(01|04|07|10)", api_version) or (not read_only and not token.strip()):
            raise CaptureError("Missing credential or API version")
        if not extraction_id or not isinstance(search_filter, str) or not search_filter.strip():
            raise CaptureError("Explicit extraction identity and scope are required")
        if not 1 <= page_size <= 100 or not 1 <= timeout_seconds <= 1200 or not 1 <= max_pages <= 10000:
            raise CaptureError("Invalid capture bounds")
        if not 1 <= max_bytes <= 256 * 1024 * 1024:
            raise CaptureError("Invalid capture resource limit")
        gid(shop_gid, "Shop")
        self.bucket, self.domain, self._token = bucket, domain, token.strip()
        self.api_version, self.search_filter, self.page_size = api_version, search_filter, page_size
        plan = compile_payments_queries(tender_source, balance_source, disputes_source)
        self.operations = dict(zip(("tenderTransactions", "balanceTransactions", "disputes"),
                                   plan.documents()))
        self.filtered = {"tenderTransactions", "disputes"}
        self.binding = {
            "format_version": 1, "stream": "payments", "domain": domain, "shop_gid": shop_gid,
            "api_version": api_version, "extraction_id": extraction_id,
            "query_sha256": {"tenderTransactions": digest(tender_source.encode()),
                             "balanceTransactions": digest(balance_source.encode()),
                             "disputes": digest(disputes_source.encode())},
            "plan_sha256": digest(encoded(self.operations)),
            "scope_sha256": digest(encoded({"query": search_filter, "first": page_size})),
        }
        key = digest(encoded([shop_gid, extraction_id, "payments"]))
        self.prefix = f"pages/v1/payments/{key}"
        self.read_only = read_only
        self.deadline = time.monotonic() + timeout_seconds
        self.max_pages, self.max_bytes = max_pages, max_bytes
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
                raise CaptureError("Extraction identity is already bound to another payments plan")

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
                        raise CaptureError("Shopify payments page request failed or API version mismatched")
                    body = bytearray()
                    for chunk in response.iter_content(chunk_size=65536):
                        if time.monotonic() >= self.deadline:
                            raise CaptureError("Payments capture deadline reached")
                        body.extend(chunk)
                        if len(body) > 2 * 1024 * 1024:
                            raise CaptureError("HTTP response exceeds capture limit")
                    return bytes(body)
        except CaptureError:
            raise
        except Exception:
            raise CaptureError("Shopify payments transport failed; response details suppressed") from None

    def fetch(self, operation, variables):
        if operation not in self.operations:
            raise CaptureError("Unknown payments operation")
        if self._finished or time.monotonic() >= self.deadline or len(self.pages) >= self.max_pages:
            raise CaptureError("Payments capture deadline, page limit, or sealed capture reached")
        request_hash = digest(encoded({"query": self.operations[operation], "variables": variables}))
        if request_hash in self._request_keys:
            raise CaptureError("Duplicate payments page request within traversal")
        name = f"{self.prefix}/{request_hash}.json"
        existing = self.bucket.get_blob(name)
        if existing is None:
            if self.read_only:
                raise CaptureError("Missing payments page in read-only capture")
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
            raise CaptureError("Missing or oversized captured payments page")
        body = existing.download_as_bytes(if_generation_match=int(existing.generation))
        metadata = existing.metadata or {}
        if metadata.get("response_sha256") != digest(body) or metadata.get("request_sha256") != request_hash:
            raise CaptureError("Captured payments page checksum or identity mismatch")
        try:
            if datetime.fromisoformat(metadata["captured_at"]).utcoffset() is None:
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            raise CaptureError("Captured payments page timestamp is missing or invalid") from None
        self.bytes += len(body)
        if self.bytes > self.max_bytes:
            raise CaptureError("Total payments capture size limit reached")
        data = decode(body)
        if data.get("errors") or not isinstance(data.get("data"), dict):
            raise CaptureError("Captured payments GraphQL response is incomplete or failed")
        self.pages.append({"uri": f"gs://{self.bucket.name}/{name}", "generation": str(existing.generation),
                           "sha256": digest(body), "request_sha256": request_hash, "operation": operation,
                           "variables": dict(variables), "captured_at": metadata["captured_at"]})
        self._request_keys.add(request_hash)
        return data["data"]

    def _connection(self, operation, data):
        if operation == "tenderTransactions":
            return data.get(operation)
        account = data.get("shopifyPaymentsAccount")
        if not isinstance(account, dict):
            raise CaptureError("Shopify Payments account is unavailable in this extraction")
        return account.get(operation)

    def walk(self, operation):
        after, cursors, identifiers = None, set(), set()
        while True:
            variables = {"first": self.page_size, "after": after}
            if operation in self.filtered:
                variables["query"] = self.search_filter
            data = self.fetch(operation, variables)
            connection = self._connection(operation, data)
            if not isinstance(connection, dict):
                raise CaptureError("Missing requested payments connection")
            info, edges = connection.get("pageInfo"), connection.get("edges")
            if (not isinstance(info, dict) or type(info.get("hasNextPage")) is not bool
                    or not isinstance(edges, list) or len(edges) > self.page_size):
                raise CaptureError("Invalid payments pagination response")
            next_cursor = info.get("endCursor")
            if info["hasNextPage"] and not edges:
                raise CaptureError("Nonadvancing payments pagination cursor")
            if info["hasNextPage"] and (not isinstance(next_cursor, str) or not next_cursor or next_cursor in cursors):
                raise CaptureError("Nonadvancing payments pagination cursor")
            for edge in edges:
                node = edge.get("node") if isinstance(edge, dict) else None
                if not isinstance(node, dict) or not isinstance(node.get("id"), str):
                    raise CaptureError("Invalid payments object identity")
                if node["id"] in identifiers:
                    raise CaptureError("Duplicate payments object across pages")
                identifiers.add(node["id"])
                yield node
            if not info["hasNextPage"]:
                return
            cursors.add(next_cursor)
            after = next_cursor

    def collect(self):
        operations = ("tenderTransactions", "balanceTransactions", "disputes")
        counts = {operation: 0 for operation in operations}
        for operation in operations:
            for _ in self.walk(operation):
                counts[operation] += 1
        seal = {"binding": self.binding, "status": "captured", "pages": self.pages,
                "counts": counts, "response_bytes": self.bytes,
                "consistency": "multi_request_observations_not_transactional_snapshot"}
        content = encoded(seal)
        blob = self.bucket.blob(f"{self.prefix}/complete.json")
        if self.read_only:
            existing = self.bucket.get_blob(blob.name)
            if existing is None or existing.download_as_bytes(if_generation_match=int(existing.generation)) != content:
                raise CaptureError("Missing or conflicting payments completion seal")
        else:
            try:
                blob.upload_from_string(content, content_type="application/json", if_generation_match=0)
            except PreconditionFailed:
                blob.reload()
                if blob.download_as_bytes(if_generation_match=int(blob.generation)) != content:
                    raise CaptureError("Conflicting completed payments capture")
        self._finished = True
        return seal
