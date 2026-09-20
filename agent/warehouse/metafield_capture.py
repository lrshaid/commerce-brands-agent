"""Immutable paginated capture for the orders/products/variants metafields."""
from datetime import datetime, timezone
import re
import time
import requests
from google.api_core.exceptions import PreconditionFailed

from .metafield_queries import compile_metafield_queries
from .refund_capture import CaptureError, decode, digest, encoded, gid


class MetafieldCapture:
    def __init__(self, *, bucket, domain, token, api_version, shop_gid, extraction_id,
                 orders_query, products_query, variants_query, search_filter,
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
        plan = compile_metafield_queries(orders_query, products_query, variants_query)
        self.operations = dict(zip(("orders", "products", "productVariants",
                                    "orderMetafields", "productMetafields", "variantMetafields"),
                                   plan.documents()))
        self._page_operations = {"metafield_orders": "orderMetafields",
                                 "metafield_products": "productMetafields",
                                 "metafield_product_variants": "variantMetafields"}
        self._owner_operations = {"orderMetafields": ("orders", "Order"),
                                  "productMetafields": ("products", "Product"),
                                  "variantMetafields": ("productVariants", "ProductVariant")}
        self.binding = {
            "format_version": 1, "stream": "metafields", "domain": domain, "shop_gid": shop_gid,
            "api_version": api_version, "extraction_id": extraction_id,
            "query_sha256": {name: digest(source.encode()) for name, source in
                             (("orders", orders_query), ("products", products_query),
                              ("productVariants", variants_query))},
            "plan_sha256": digest(encoded(self.operations)),
            "scope_sha256": digest(encoded({"query": search_filter, "first": page_size})),
        }
        for stream, field in (("metafield_orders", "order_metafields"),
                              ("metafield_products", "product_metafields"),
                              ("metafield_product_variants", "variant_metafields")):
            self.binding[f"{stream}_query_sha256"] = digest(getattr(plan, field).encode())
        key = digest(encoded([shop_gid, extraction_id, "metafields"]))
        self.prefix = f"pages/v1/metafields/{key}"
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
                raise CaptureError("Extraction identity is already bound to another metafield plan")

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
                        raise CaptureError("Shopify metafield page request failed or API version mismatched")
                    body = bytearray()
                    for chunk in response.iter_content(chunk_size=65536):
                        if time.monotonic() >= self.deadline:
                            raise CaptureError("Metafield capture deadline reached")
                        body.extend(chunk)
                        if len(body) > 2 * 1024 * 1024:
                            raise CaptureError("HTTP response exceeds capture limit")
                    return bytes(body)
        except CaptureError:
            raise
        except Exception:
            raise CaptureError("Shopify metafield transport failed; response details suppressed") from None

    def fetch(self, operation, variables):
        if operation not in self.operations:
            raise CaptureError("Unknown metafield operation")
        if self._finished or time.monotonic() >= self.deadline or len(self.pages) >= self.max_pages:
            raise CaptureError("Metafield capture deadline, page limit, or sealed capture reached")
        request_hash = digest(encoded({"query": self.operations[operation], "variables": variables}))
        if request_hash in self._request_keys:
            raise CaptureError("Duplicate metafield page request within traversal")
        name = f"{self.prefix}/{request_hash}.json"
        existing = self.bucket.get_blob(name)
        if existing is None:
            if self.read_only:
                raise CaptureError("Missing metafield page in read-only capture")
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
            raise CaptureError("Missing or oversized captured metafield page")
        body = existing.download_as_bytes(if_generation_match=int(existing.generation))
        metadata = existing.metadata or {}
        if metadata.get("response_sha256") != digest(body) or metadata.get("request_sha256") != request_hash:
            raise CaptureError("Captured metafield page checksum or identity mismatch")
        try:
            if datetime.fromisoformat(metadata["captured_at"]).utcoffset() is None:
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            raise CaptureError("Captured metafield page timestamp is missing or invalid") from None
        self.bytes += len(body)
        if self.bytes > self.max_bytes:
            raise CaptureError("Total metafield capture size limit reached")
        data = decode(body)
        if data.get("errors") or not isinstance(data.get("data"), dict):
            raise CaptureError("Captured metafield GraphQL response is incomplete or failed")
        self.pages.append({"uri": f"gs://{self.bucket.name}/{name}", "generation": str(existing.generation),
                           "sha256": digest(body), "request_sha256": request_hash, "operation": operation,
                           "variables": dict(variables), "captured_at": metadata["captured_at"]})
        self._request_keys.add(request_hash)
        return data["data"]

    def walk(self, operation, owner=None):
        after, cursors, identifiers = None, set(), set()
        while True:
            if operation in self._owner_operations:
                variables = {"first": self.page_size, "after": after,
                             "id": gid(owner, self._owner_operations[operation][1])}
            else:
                variables = {"first": self.page_size, "after": after,
                             "query": self.search_filter}
            data = self.fetch(operation, variables)
            if operation in self._owner_operations:
                node = data.get("node")
                if not isinstance(node, dict) or node.get("id") != owner:
                    raise CaptureError("Metafield page owner mismatch")
                connection = node.get("metafields")
                items = connection.get("nodes") if isinstance(connection, dict) else None
            else:
                connection = data.get(operation)
                items = connection.get("nodes") if isinstance(connection, dict) else None
            if not isinstance(connection, dict) or not isinstance(items, list):
                raise CaptureError("Missing requested metafield connection")
            info = connection.get("pageInfo")
            if not isinstance(info, dict) or type(info.get("hasNextPage")) is not bool:
                raise CaptureError("Invalid metafield pagination response")
            next_cursor = info.get("endCursor")
            if info["hasNextPage"] and not items:
                raise CaptureError("Nonadvancing metafield pagination cursor")
            if info["hasNextPage"] and (not isinstance(next_cursor, str) or not next_cursor or next_cursor in cursors):
                raise CaptureError("Nonadvancing metafield pagination cursor")
            for item in items:
                if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                    raise CaptureError("Invalid metafield object identity")
                if item["id"] in identifiers:
                    raise CaptureError("Duplicate metafield object across pages")
                identifiers.add(item["id"])
                yield item
            if not info["hasNextPage"]:
                return
            cursors.add(next_cursor)
            after = next_cursor

    def collect(self):
        counts = {name: 0 for name in ("orders", "products", "productVariants",
                                       "orderMetafields", "productMetafields", "variantMetafields")}
        metafield_owners = {}
        # Identity pages carry owner ids only; metafield pages are the only
        # published transport rows. Identity pages live in the seal for audit
        # and lineage. Each family walks its root once and pages each owner's
        # metafields inline, so no owner id is held in memory and no page is
        # requested twice.
        for root_operation, page_operation, owner_type in (
                ("orders", "orderMetafields", "Order"),
                ("products", "productMetafields", "Product"),
                ("productVariants", "variantMetafields", "ProductVariant")):
            for owner in self.walk(root_operation):
                owner_id = gid(owner["id"], owner_type)
                counts[root_operation] += 1
                for metafield in self.walk(page_operation, owner_id):
                    previous_owner = metafield_owners.setdefault(metafield["id"], owner_id)
                    if previous_owner != owner_id:
                        raise CaptureError("Metafield is linked to multiple owners in one extraction")
                    counts[page_operation] += 1
        seal = {"binding": self.binding, "status": "captured", "pages": self.pages,
                "counts": counts, "response_bytes": self.bytes,
                "consistency": "multi_request_observations_not_transactional_snapshot"}
        content = encoded(seal)
        blob = self.bucket.blob(f"{self.prefix}/complete.json")
        if self.read_only:
            existing = self.bucket.get_blob(blob.name)
            if existing is None or existing.download_as_bytes(if_generation_match=int(existing.generation)) != content:
                raise CaptureError("Missing or conflicting metafield completion seal")
        else:
            try:
                blob.upload_from_string(content, content_type="application/json", if_generation_match=0)
            except PreconditionFailed:
                blob.reload()
                if blob.download_as_bytes(if_generation_match=int(blob.generation)) != content:
                    raise CaptureError("Conflicting completed metafield capture")
        self._finished = True
        return seal
