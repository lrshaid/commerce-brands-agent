"""Pinned capture for updated fulfillment orders and all of their line items."""
from datetime import datetime, timezone
import re
import time

import requests
from google.api_core.exceptions import PreconditionFailed

from .fulfillment_orders_queries import compile_fulfillment_order_queries
from .refund_capture import CaptureError, decode, digest, encoded, gid


class FulfillmentOrdersCapture:
    def __init__(self, *, bucket, domain, token, api_version, shop_gid, extraction_id,
                 query_source, window_start, window_end, page_size=50,
                 timeout_seconds=900, max_pages=2000,
                 max_bytes=256 * 1024 * 1024, read_only=False):
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.myshopify\.com", domain):
            raise CaptureError("Invalid Shopify domain")
        if not re.fullmatch(r"20[0-9]{2}-(01|04|07|10)", api_version) or (not read_only and not token.strip()):
            raise CaptureError("Missing credential or API version")
        if not extraction_id or not 1 <= page_size <= 100 or not 1 <= timeout_seconds <= 1200:
            raise CaptureError("Explicit extraction identity and valid bounds are required")
        if not 1 <= max_pages <= 10000 or not 1 <= max_bytes <= 256 * 1024 * 1024:
            raise CaptureError("Invalid capture resource limit")
        try:
            start = datetime.fromisoformat(window_start.replace("Z", "+00:00"))
            end = datetime.fromisoformat(window_end.replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            raise CaptureError("Invalid fulfillment-order extraction window") from None
        if start.utcoffset() is None or end.utcoffset() is None or start >= end:
            raise CaptureError("Invalid fulfillment-order extraction window")
        gid(shop_gid, "Shop")
        plan = compile_fulfillment_order_queries(query_source)
        self.operations = dict(zip(("fulfillmentOrders", "lineItems"), plan.documents()))
        self.binding = {
            "format_version": 1, "stream": "fulfillment_orders", "domain": domain,
            "shop_gid": shop_gid, "api_version": api_version, "extraction_id": extraction_id,
            "query_sha256": digest(query_source.encode()),
            "plan_sha256": digest(encoded(self.operations)),
            "scope_sha256": digest(encoded({"window_start": start.isoformat(),
                                             "window_end": end.isoformat(), "first": page_size})),
        }
        key = digest(encoded([shop_gid, extraction_id, "fulfillment_orders"]))
        self.prefix = f"pages/v1/fulfillment_orders/{key}"
        self.bucket, self.domain, self._token = bucket, domain, token.strip()
        self.api_version, self.page_size = api_version, page_size
        self.window_start, self.window_end = start, end
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
                raise CaptureError("Missing or conflicting read-only fulfillment-orders binding")
            return
        try:
            blob.upload_from_string(encoded(self.binding), content_type="application/json", if_generation_match=0)
        except PreconditionFailed:
            blob.reload()
            if decode(blob.download_as_bytes(if_generation_match=int(blob.generation))) != self.binding:
                raise CaptureError("Extraction identity is already bound to another fulfillment-orders plan")

    def _http(self, document, variables):
        if self.read_only:
            raise CaptureError("Read-only capture cannot call Shopify")
        try:
            with requests.Session() as session:
                session.trust_env = False
                with session.post(f"https://{self.domain}/admin/api/{self.api_version}/graphql.json",
                                  json={"query": document, "variables": variables},
                                  headers={"X-Shopify-Access-Token": self._token}, stream=True,
                                  allow_redirects=False, timeout=(10, 30)) as response:
                    if response.status_code != 200 or response.headers.get("X-Shopify-API-Version") != self.api_version:
                        raise CaptureError("Shopify fulfillment-orders request failed or API version mismatched")
                    body = bytearray()
                    for chunk in response.iter_content(chunk_size=65536):
                        if time.monotonic() >= self.deadline:
                            raise CaptureError("Fulfillment-orders capture deadline reached")
                        body.extend(chunk)
                        if len(body) > 2 * 1024 * 1024:
                            raise CaptureError("HTTP response exceeds capture limit")
                    return bytes(body)
        except CaptureError:
            raise
        except Exception:
            raise CaptureError("Shopify fulfillment-orders transport failed; details suppressed") from None

    def fetch(self, operation, variables):
        if operation not in self.operations or self._finished or time.monotonic() >= self.deadline:
            raise CaptureError("Unknown operation, deadline, or sealed fulfillment-orders capture")
        if len(self.pages) >= self.max_pages:
            raise CaptureError("Fulfillment-orders page limit reached")
        request_hash = digest(encoded({"query": self.operations[operation], "variables": variables}))
        if request_hash in self._request_keys:
            raise CaptureError("Duplicate fulfillment-orders page request")
        name = f"{self.prefix}/{request_hash}.json"
        existing = self.bucket.get_blob(name)
        if existing is None:
            if self.read_only:
                raise CaptureError("Missing page in read-only fulfillment-orders capture")
            body = self._http(self.operations[operation], variables)
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
            raise CaptureError("Missing or oversized captured fulfillment-orders page")
        body = existing.download_as_bytes(if_generation_match=int(existing.generation))
        metadata = existing.metadata or {}
        if metadata.get("response_sha256") != digest(body) or metadata.get("request_sha256") != request_hash:
            raise CaptureError("Captured fulfillment-orders page identity mismatch")
        data = decode(body)
        if data.get("errors") or not isinstance(data.get("data"), dict):
            raise CaptureError("Captured fulfillment-orders response is incomplete")
        self.bytes += len(body)
        if self.bytes > self.max_bytes:
            raise CaptureError("Total fulfillment-orders capture size limit reached")
        self.pages.append({"uri": f"gs://{self.bucket.name}/{name}", "generation": str(existing.generation),
                           "sha256": digest(body), "request_sha256": request_hash,
                           "operation": operation, "variables": dict(variables),
                           "captured_at": metadata.get("captured_at")})
        self._request_keys.add(request_hash)
        return data["data"]

    def _connection(self, operation, owner=None):
        after, cursors, identifiers = None, set(), set()
        while True:
            variables = {"first": self.page_size, "after": after}
            if owner is not None:
                variables["id"] = owner
            data = self.fetch(operation, variables)
            if owner is None:
                connection = data.get("fulfillmentOrders")
            else:
                node = data.get("node")
                if not isinstance(node, dict) or node.get("id") != owner:
                    raise CaptureError("Fulfillment-order line page owner mismatch")
                connection = node.get("lineItems")
            if not isinstance(connection, dict):
                raise CaptureError("Missing fulfillment-orders connection")
            info, edges = connection.get("pageInfo"), connection.get("edges")
            if (not isinstance(info, dict) or type(info.get("hasNextPage")) is not bool
                    or not isinstance(edges, list) or len(edges) > self.page_size):
                raise CaptureError("Invalid fulfillment-orders pagination response")
            for edge in edges:
                item = edge.get("node") if isinstance(edge, dict) else None
                if not isinstance(item, dict) or not isinstance(item.get("id"), str) or item["id"] in identifiers:
                    raise CaptureError("Invalid or duplicate fulfillment-order object")
                identifiers.add(item["id"])
                yield item
            cursor = info.get("endCursor")
            if not info["hasNextPage"]:
                return
            if not isinstance(cursor, str) or not cursor or cursor in cursors or not edges:
                raise CaptureError("Nonadvancing fulfillment-orders cursor")
            cursors.add(cursor)
            after = cursor

    def collect(self):
        counts = {"fulfillmentOrders": 0, "lineItems": 0}
        line_owners = {}
        for order in self._connection("fulfillmentOrders"):
            try:
                updated_at = datetime.fromisoformat(order["updatedAt"].replace("Z", "+00:00"))
            except (KeyError, AttributeError, ValueError):
                raise CaptureError("Fulfillment order has an invalid updatedAt") from None
            if updated_at >= self.window_end:
                continue
            if updated_at < self.window_start:
                break
            order_id = gid(order.get("id"), "FulfillmentOrder")
            gid(order.get("order", {}).get("id"), "Order")
            counts["fulfillmentOrders"] += 1
            for line in self._connection("lineItems", order_id):
                line_id = gid(line.get("id"), "FulfillmentOrderLineItem")
                previous = line_owners.setdefault(line_id, order_id)
                if previous != order_id:
                    raise CaptureError("Fulfillment-order line is linked to multiple owners")
                counts["lineItems"] += 1
        seal = {"binding": self.binding, "status": "captured", "pages": self.pages,
                "counts": counts, "response_bytes": self.bytes,
                "consistency": "multi_request_observations_not_transactional_snapshot"}
        content = encoded(seal)
        blob = self.bucket.blob(f"{self.prefix}/complete.json")
        if self.read_only:
            existing = self.bucket.get_blob(blob.name)
            if existing is None or existing.download_as_bytes(if_generation_match=int(existing.generation)) != content:
                raise CaptureError("Missing or conflicting fulfillment-orders completion seal")
        else:
            try:
                blob.upload_from_string(content, content_type="application/json", if_generation_match=0)
            except PreconditionFailed:
                blob.reload()
                if blob.download_as_bytes(if_generation_match=int(blob.generation)) != content:
                    raise CaptureError("Conflicting completed fulfillment-orders capture")
        self._finished = True
        return seal
