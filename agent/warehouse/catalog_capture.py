"""Immutable paginated capture for the customers/products catalog streams."""
from datetime import datetime, timezone
import re
import time
import requests
from google.api_core.exceptions import PreconditionFailed

from .refund_capture import CaptureError, decode, digest, encoded, gid
from .catalog_queries import compile_catalog_queries


class CatalogCapture:
    def __init__(self, *, bucket, domain, token, api_version, shop_gid, extraction_id,
                 customer_query, product_query, search_filter, page_size=50,
                 timeout_seconds=900, max_pages=2000, max_bytes=256 * 1024 * 1024,
                 read_only=False):
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
        plan = compile_catalog_queries(customer_query, product_query)
        self.operations = dict(zip(("customers", "products", "variants"), plan.documents()))
        self.binding = {
            "format_version": 1, "stream": "catalog", "domain": domain, "shop_gid": shop_gid,
            "api_version": api_version, "extraction_id": extraction_id,
            "query_sha256": {"customers": digest(customer_query.encode()), "products": digest(product_query.encode())},
            "plan_sha256": digest(encoded(self.operations)),
            "scope_sha256": digest(encoded({"query": search_filter, "first": page_size})),
        }
        self.binding["variant_query_sha256"] = digest(encoded(self.operations["variants"]))
        key = digest(encoded([shop_gid, extraction_id, "catalog"]))
        self.prefix = f"pages/v1/catalog/{key}"
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
                raise CaptureError("Extraction identity is already bound to another catalog plan")

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
                        raise CaptureError("Shopify catalog page request failed or API version mismatched")
                    body = bytearray()
                    for chunk in response.iter_content(chunk_size=65536):
                        if time.monotonic() >= self.deadline:
                            raise CaptureError("Catalog capture deadline reached")
                        body.extend(chunk)
                        if len(body) > 2 * 1024 * 1024:
                            raise CaptureError("HTTP response exceeds capture limit")
                    return bytes(body)
        except CaptureError:
            raise
        except Exception:
            raise CaptureError("Shopify catalog transport failed; response details suppressed") from None

    def fetch(self, operation, variables):
        if operation not in self.operations:
            raise CaptureError("Unknown catalog operation")
        if self._finished or time.monotonic() >= self.deadline or len(self.pages) >= self.max_pages:
            raise CaptureError("Catalog capture deadline, page limit, or sealed capture reached")
        request_hash = digest(encoded({"query": self.operations[operation], "variables": variables}))
        if request_hash in self._request_keys:
            raise CaptureError("Duplicate catalog page request within traversal")
        name = f"{self.prefix}/{request_hash}.json"
        existing = self.bucket.get_blob(name)
        if existing is None:
            if self.read_only:
                raise CaptureError("Missing catalog page in read-only capture")
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
            raise CaptureError("Missing or oversized captured catalog page")
        body = existing.download_as_bytes(if_generation_match=int(existing.generation))
        metadata = existing.metadata or {}
        if metadata.get("response_sha256") != digest(body) or metadata.get("request_sha256") != request_hash:
            raise CaptureError("Captured catalog page checksum or identity mismatch")
        try:
            if datetime.fromisoformat(metadata["captured_at"]).utcoffset() is None:
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            raise CaptureError("Captured catalog page timestamp is missing or invalid") from None
        self.bytes += len(body)
        if self.bytes > self.max_bytes:
            raise CaptureError("Total catalog capture size limit reached")
        data = decode(body)
        if data.get("errors") or not isinstance(data.get("data"), dict):
            raise CaptureError("Captured catalog GraphQL response is incomplete or failed")
        self.pages.append({"uri": f"gs://{self.bucket.name}/{name}", "generation": str(existing.generation),
                           "sha256": digest(body), "request_sha256": request_hash, "operation": operation,
                           "variables": dict(variables), "captured_at": metadata["captured_at"]})
        self._request_keys.add(request_hash)
        return data["data"]

    def walk(self, operation, owner=None):
        after, cursors, identifiers = None, set(), set()
        while True:
            variables = {"first": self.page_size, "after": after}
            if operation in ("customers", "products"):
                variables["query"] = self.search_filter
            else:
                variables["id"] = gid(owner, "Product")
            data = self.fetch(operation, variables)
            if operation in ("customers", "products"):
                connection = data.get(operation)
                items = connection.get("nodes") if isinstance(connection, dict) else None
            else:
                node = data.get("node")
                if not isinstance(node, dict) or node.get("id") != owner:
                    raise CaptureError("Catalog page owner mismatch")
                connection = node.get("variants")
                items = connection.get("nodes") if isinstance(connection, dict) else None
            if not isinstance(connection, dict) or not isinstance(items, list):
                raise CaptureError("Missing requested catalog connection")
            info = connection.get("pageInfo")
            if not isinstance(info, dict) or type(info.get("hasNextPage")) is not bool:
                raise CaptureError("Invalid catalog pagination response")
            next_cursor = info.get("endCursor")
            if info["hasNextPage"] and not items:
                raise CaptureError("Nonadvancing catalog pagination cursor")
            if info["hasNextPage"] and (not isinstance(next_cursor, str) or not next_cursor or next_cursor in cursors):
                raise CaptureError("Nonadvancing catalog pagination cursor")
            for item in items:
                if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                    raise CaptureError("Invalid catalog object identity")
                if item["id"] in identifiers:
                    raise CaptureError("Duplicate catalog object across pages")
                identifiers.add(item["id"])
                yield item
            if not info["hasNextPage"]:
                return
            cursors.add(next_cursor)
            after = next_cursor

    def collect(self):
        counts = {"customers": 0, "products": 0, "variants": 0}
        variant_owners = {}
        for _ in self.walk("customers"):
            counts["customers"] += 1
        for product in self.walk("products"):
            counts["products"] += 1
            product_id = gid(product.get("id"), "Product")
            for variant in self.walk("variants", product_id):
                variant_id = gid(variant.get("id"), "ProductVariant")
                previous_owner = variant_owners.setdefault(variant_id, product_id)
                if previous_owner != product_id:
                    raise CaptureError("Product variant is linked to multiple products in one extraction")
                counts["variants"] += 1
        seal = {"binding": self.binding, "status": "captured", "pages": self.pages,
                "counts": counts, "response_bytes": self.bytes,
                "consistency": "multi_request_observations_not_transactional_snapshot"}
        content = encoded(seal)
        blob = self.bucket.blob(f"{self.prefix}/complete.json")
        if self.read_only:
            existing = self.bucket.get_blob(blob.name)
            if existing is None or existing.download_as_bytes(if_generation_match=int(existing.generation)) != content:
                raise CaptureError("Missing or conflicting catalog completion seal")
        else:
            try:
                blob.upload_from_string(content, content_type="application/json", if_generation_match=0)
            except PreconditionFailed:
                blob.reload()
                if blob.download_as_bytes(if_generation_match=int(blob.generation)) != content:
                    raise CaptureError("Conflicting completed catalog capture")
        self._finished = True
        return seal
