"""Bulk headers + batches of five Refunds, with independent exhaustive top-ups.

V1 remains available for old immutable captures. No reconstruction is stored as raw.
"""
from datetime import datetime, timezone
from io import BytesIO
import time

from google.api_core.exceptions import PreconditionFailed

from .refund_capture import RefundCapture, CaptureError, decode, digest, encoded, gid
from .refund_queries_v2 import compile_refund_queries_v2, REFUND_CONNECTIONS, RETURN_CONNECTIONS
from .raw_records import ExtractionIdentity, iter_raw_records
from .shopify_bulk import BulkClient
from .shopify_export import wait_for_export, download_export


class QueryCostLimit(CaptureError):
    pass


class RefundCaptureV2(RefundCapture):
    def __init__(self, *, bucket, domain, token, api_version, shop_gid, extraction_id,
                 query_source, search_filter, page_size=50, timeout_seconds=1200,
                 max_pages=10000, max_bytes=256*1024*1024, read_only=False):
        # Reuse the proven HTTP-page store, with a separately bound v2 plan.
        if api_version != "2026-04":
            raise CaptureError("Refund v2 projection is validated for API 2026-04 only")
        if page_size != 50:
            raise CaptureError("Refund v2 requires page size 50")
        if not read_only:
            BulkClient(domain, token, api_version)  # Validate credential/domain without I/O.
        else:
            BulkClient(domain, "read-only", api_version)
        gid(shop_gid, "Shop")
        if not extraction_id or not search_filter.strip() or not 1 <= timeout_seconds <= 1200:
            raise CaptureError("Explicit extraction, scope and bounded timeout required")
        if not 1 <= max_pages <= 10000 or not 1 <= max_bytes <= 256*1024*1024:
            raise CaptureError("Invalid resource limits")
        plan = compile_refund_queries_v2(query_source)
        self.operations = plan.operations()
        self.bulk_source = plan.bulk
        self.binding = dict(format_version=2, domain=domain, shop_gid=shop_gid, search_filter=search_filter,
            api_version=api_version, extraction_id=extraction_id, query_sha256=digest(query_source.encode()),
            plan_sha256=digest(encoded({"bulk": plan.bulk, **self.operations})),
            scope_sha256=digest(encoded({"query": search_filter, "first": page_size, "batch_size": 5})))
        self.prefix = "pages/v2/order_refunds/" + digest(encoded([shop_gid, extraction_id]))
        self.bucket, self.domain, self._token = bucket, domain, token.strip()
        self.api_version, self.search_filter, self.page_size = api_version, search_filter, page_size
        self.read_only = read_only
        self.deadline = time.monotonic() + timeout_seconds
        self.max_pages, self.max_bytes = max_pages, max_bytes
        self.pages, self.bytes = [], 0
        self._request_keys, self._finished = set(), False
        self._bind()

    def _http(self, document, variables):
        # Only read operations are retried. Never retry a Bulk submission here.
        for attempt in range(4):
            try:
                body = super()._http(document, variables)
                errors = decode(body).get("errors")
                if not errors:
                    return body
                if all(e.get("extensions", {}).get("code") == "MAX_COST_EXCEEDED" for e in errors):
                    raise QueryCostLimit("Reduce read page size to meet Shopify query cost")
                if not all(e.get("extensions", {}).get("code") == "THROTTLED" for e in errors):
                    raise CaptureError("Shopify rejected refund projection; no partial data accepted")
            except QueryCostLimit:
                raise
            except CaptureError:
                if attempt == 3:
                    raise
            delay = min(2 ** (attempt + 1), 8)
            if time.monotonic() + delay >= self.deadline:
                raise CaptureError("Capture deadline reached during retry")
            time.sleep(delay)
        raise CaptureError("Shopify read retry limit reached")

    def _fetch_sized(self, operation, variables):
        # Start at 50; large projections can exceed Shopify's 1000-point limit.
        # Saved successful request size is discoverable without repeating failed HTTP calls.
        for size in (50, 25, 12, 6, 3, 1):
            candidate = dict(variables, first=size)
            key = digest(encoded({"query": self.operations[operation], "variables": candidate}))
            saved = self.bucket.get_blob(f"{self.prefix}/{key}.json")
            if saved is not None:
                return self.fetch(operation, candidate)
        if self.read_only:
            raise CaptureError("Missing successful page for read-only replay")
        for size in (50, 25, 12, 6, 3, 1):
            try:
                return self.fetch(operation, dict(variables, first=size))
            except QueryCostLimit:
                continue
        raise CaptureError("Projection exceeds query cost even at page size 1")

    def _immutable(self, name, content):
        blob = self.bucket.blob(name)
        try:
            blob.upload_from_string(content, content_type="application/json", if_generation_match=0)
        except PreconditionFailed:
            blob = self.bucket.get_blob(name)
            if blob.download_as_bytes(if_generation_match=int(blob.generation)) != content:
                raise CaptureError("Conflicting immutable capture file")
        return blob

    def _bulk(self):
        descriptor = self.bucket.get_blob(self.prefix + "/bulk.json")
        if descriptor is None:
            if self.read_only:
                raise CaptureError("Missing saved bulk descriptor")
            client = BulkClient(self.domain, self._token, self.api_version)
            op = client.submit_once(bucket=self.bucket,
                extraction_id="refunds-v2:" + self.binding["extraction_id"],
                query_source=self.bulk_source, search_filter=self.search_filter)
            remaining = int(self.deadline-time.monotonic())
            if remaining < 1:
                raise CaptureError("Capture deadline reached")
            export = wait_for_export(client, op, timeout_seconds=min(1200, remaining))
            with download_export(export, max_file_bytes=self.max_bytes) as source:
                body = source.read()
            self._validate_bulk(body, export.object_count, export.root_count)
            blob = self._immutable(self.prefix + "/orders.jsonl", body)
            ref = dict(uri=f"gs://{self.bucket.name}/{blob.name}", generation=str(blob.generation),
                sha256=digest(body), role="bulk_headers", operation="orders",
                captured_at=export.completed_at.isoformat(), started_at=export.created_at.isoformat(),
                operation_id=op, record_count=export.object_count, root_count=export.root_count)
            descriptor = self._immutable(self.prefix + "/bulk.json", encoded(ref))
        ref = decode(descriptor.download_as_bytes(if_generation_match=int(descriptor.generation)))
        name = ref["uri"].removeprefix(f"gs://{self.bucket.name}/")
        if name != self.prefix + "/orders.jsonl":
            raise CaptureError("Invalid bulk reference")
        blob = self.bucket.get_blob(name)
        if blob is None or str(blob.generation) != ref["generation"] or blob.size > self.max_bytes:
            raise CaptureError("Missing or oversized bulk file")
        body = blob.download_as_bytes(if_generation_match=int(ref["generation"]))
        if digest(body) != ref["sha256"]:
            raise CaptureError("Bulk checksum mismatch")
        orders = self._validate_bulk(body, int(ref["record_count"]), int(ref["root_count"]))
        self.bytes += len(body)
        if self.bytes > self.max_bytes:
            raise CaptureError("Capture size limit reached")
        self.bulk_reference = ref
        return orders

    def _validate_bulk(self, body, expected, roots):
        identity = ExtractionIdentity(self.binding["shop_gid"], self.binding["extraction_id"],
            "pending", self.binding["query_sha256"], digest(encoded(self.binding)), self.api_version,
            datetime.now(timezone.utc))
        orders, seen, refunds_seen = [], set(), set()
        for row in iter_raw_records(BytesIO(body), identity):
            order = decode(row["record_text"].encode())
            order_id = gid(order.get("id"), "Order")
            if row["parent_gid"] is not None or order_id in seen or not isinstance(order.get("refunds"), list):
                raise CaptureError("Invalid or duplicate bulk order")
            seen.add(order_id)
            for refund in order["refunds"]:
                refund_id = gid(refund.get("id"), "Refund")
                if refund_id in refunds_seen:
                    raise CaptureError("Duplicate refund in bulk headers")
                refunds_seen.add(refund_id)
            orders.append(order)
        if len(orders) != expected or len(orders) != roots:
            raise CaptureError("Bulk header counts differ from provider")
        return orders

    def _connection(self, connection, seen, cursors):
        if not isinstance(connection, dict):
            raise CaptureError("Missing connection")
        nodes, info = connection.get("nodes"), connection.get("pageInfo")
        if (not isinstance(nodes, list) or len(nodes) > self.page_size or not isinstance(info, dict)
                or type(info.get("hasNextPage")) is not bool):
            raise CaptureError("Invalid connection page")
        cursor = info.get("endCursor")
        if ((nodes and (not isinstance(cursor, str) or not cursor or cursor in cursors))
                or info["hasNextPage"] and not nodes):
            raise CaptureError("Nonadvancing connection cursor")
        for node in nodes:
            identifier = node.get("id") if isinstance(node, dict) else None
            if not isinstance(identifier, str) or not identifier or identifier in seen:
                raise CaptureError("Duplicate or missing child identity")
            seen.add(identifier)
        if cursor:
            cursors.add(cursor)
        return len(nodes), cursor if info["hasNextPage"] else None

    def _collect_connection(self, name, owner, initial):
        seen, cursors = set(), set()
        count, after = self._connection(initial, seen, cursors)
        while after:
            data = self._fetch_sized(name, {"id": owner, "first": self.page_size, "after": after}).data
            root = "return" if name in RETURN_CONNECTIONS else "refund"
            node = data.get(root)
            if not isinstance(node, dict) or node.get("id") != owner:
                raise CaptureError("Top-up owner mismatch")
            n, after = self._connection(node.get(name), seen, cursors)
            count += n
        return count

    def collect(self):
        if self._finished:
            raise CaptureError("Capture already sealed")
        orders = self._bulk()
        refs = {r["id"]: r for o in orders for r in o["refunds"]}
        counts = dict(orders=len(orders), refunds=len(refs), **{
            n: 0 for n in (*REFUND_CONNECTIONS, *RETURN_CONNECTIONS)})
        ids = list(refs)
        returns_seen = set()
        for offset in range(0, len(ids), 5):
            batch_ids = ids[offset:offset+5]
            nodes = self._fetch_sized("refundBatch", {"ids": batch_ids, "first": 50}).data.get("nodes")
            if (not isinstance(nodes, list) or len(nodes) != len(batch_ids)
                    or any(not isinstance(n, dict) for n in nodes)
                    or [n.get("id") for n in nodes] != batch_ids
                    or any(n.get("__typename") != "Refund" for n in nodes)):
                raise CaptureError("Partial or mismatched Refund batch")
            for node in nodes:
                owner = node["id"]
                for name in REFUND_CONNECTIONS:
                    counts[name] += self._collect_connection(name, owner, node.get(name))
                returned = node.get("return")
                original = refs[owner].get("return")
                if (returned or {}).get("id") != (original or {}).get("id"):
                    raise CaptureError("Return identity changed between passes")
                if returned is not None:
                    return_id = gid(returned.get("id"), "Return")
                    # Return data is observed in each batch, but top-ups and counts are once per Return.
                    if return_id not in returns_seen:
                        returns_seen.add(return_id)
                        for name in RETURN_CONNECTIONS:
                            counts[name] += self._collect_connection(name, return_id, returned.get(name))
        seal = dict(binding=self.binding, status="captured", bulk=self.bulk_reference,
            pages=self.pages, counts=counts, response_bytes=self.bytes,
            consistency="multi_request_observations_not_transactional_snapshot")
        content = encoded(seal)
        if self.read_only:
            blob = self.bucket.get_blob(self.prefix + "/complete.json")
            if blob is None or blob.download_as_bytes(if_generation_match=int(blob.generation)) != content:
                raise CaptureError("Missing or conflicting completion seal")
        else:
            self._immutable(self.prefix + "/complete.json", content)
        self._finished = True
        return seal
