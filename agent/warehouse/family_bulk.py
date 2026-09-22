"""Replayable catalog, fulfillment and inventory bulk captures.

Raw JSONL is retained byte-for-byte. Products and variants share one export,
so the variant scope remains all variants of products updated in the window.
Inventory levels remain a full location snapshot. Country customs codes are
supplemental GraphQL observations: that connection does not implement Node.
"""
from datetime import datetime, timezone
import hashlib
import io
import json
import time
from pathlib import Path

from google.api_core.exceptions import PreconditionFailed

from .raw_landing import land_jsonl
from .raw_records import ExtractionIdentity, iter_raw_records
from .shopify_bulk import bind_bulk_query
from .shopify_export import download_export, wait_for_export

QUERY_DIR = Path(__file__).resolve().parents[2] / "queries/shopify"
FAMILIES = {
    "catalog": {"customers": ("customers", "customers_bulk.graphql"),
                "products": ("products", "products_bulk.graphql")},
    "fulfillments": {"fulfillments": ("orders", "fulfillments_bulk.graphql")},
    "inventory": {"inventory_items": ("inventoryItems", "inventory_items_bulk.graphql"),
                  "inventory_levels": ("locations", "inventory_levels_bulk.graphql")},
}
STREAM_OPERATION = {"variants": "products"}
ROOT_TYPES = {"customers": "Customer", "products": "Product", "variants": "Product",
              "fulfillments": "Order", "inventory_items": "InventoryItem", "inventory_levels": "Location"}
CHILD_TYPES = {"products": "ProductVariant", "variants": "ProductVariant", "inventory_levels": "InventoryLevel"}
TRANSPORT = "shopify_bulk_query"
INVENTORY_TRANSPORT = "shopify_bulk_with_country_codes"


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def digest(body):
    return hashlib.sha256(body).hexdigest()


def _gid(value, kind):
    return isinstance(value, str) and value.startswith(f"gid://shopify/{kind}/") and bool(value.split('/')[-1])


def validate_bulk_rows(stream, records, *, object_count, root_count):
    """Reject unknown objects, duplicates, orphans and incomplete exports.

    Parent resolution is independent of physical JSONL ordering.
    """
    roots, children, seen = set(), [], set()
    count = 0
    for row in records:
        count += 1
        body = json.loads(row["record_text"])
        gid, parent = body.get("id"), body.get("__parentId")
        if not isinstance(gid, str) or gid in seen:
            raise ValueError("Missing or duplicate bulk object identity")
        seen.add(gid)
        if parent is None:
            if not _gid(gid, ROOT_TYPES[stream]):
                raise ValueError("Unexpected bulk root type")
            roots.add(gid)
            if stream == "fulfillments":
                items = body.get("fulfillments")
                if not isinstance(items, list):
                    raise ValueError("Missing fulfillments list")
                for item in items:
                    fid = item.get("id") if isinstance(item, dict) else None
                    if not _gid(fid, "Fulfillment") or fid in seen:
                        raise ValueError("Invalid or duplicate fulfillment identity")
                    seen.add(fid)
        else:
            if stream not in CHILD_TYPES or not _gid(gid, CHILD_TYPES[stream]):
                raise ValueError("Unexpected bulk child type")
            children.append(parent)
            if stream == "inventory_levels":
                if body.get("location", {}).get("id") != parent:
                    raise ValueError("Inventory level location differs from parent")
                if not _gid(body.get("item", {}).get("id"), "InventoryItem"):
                    raise ValueError("Inventory level missing item")
                quantities = body.get("quantities")
                if (not isinstance(quantities, list) or len(quantities) != 1
                        or quantities[0].get("name") != "available"
                        or type(quantities[0].get("quantity")) is not int):
                    raise ValueError("Inventory available quantity missing or invalid")
    if any(parent not in roots for parent in children):
        raise ValueError("Orphan bulk child")
    if count != object_count or len(roots) != root_count:
        raise ValueError("Bulk provider counts do not match JSONL")
    return len(roots)


def country_codes(observed, gid):
    """Validate saved supplemental observations before entity publication."""
    if observed.get("id") != gid or not isinstance(observed.get("pages"), list) or not observed["pages"]:
        raise ValueError("Invalid country codes observation")
    all_edges, seen, cursors = [], set(), set()
    for index, page in enumerate(observed["pages"]):
        node = page.get("inventoryItem", {})
        if node.get("id") != gid:
            raise ValueError("Country codes owner mismatch")
        connection = node.get("countryHarmonizedSystemCodes", {})
        edges, info = connection.get("edges"), connection.get("pageInfo", {})
        expected_more = index < len(observed["pages"]) - 1
        if not isinstance(edges, list) or info.get("hasNextPage") is not expected_more:
            raise ValueError("Incomplete country codes pagination")
        if expected_more:
            cursor = info.get("endCursor")
            if not edges or not isinstance(cursor, str) or not cursor or cursor in cursors:
                raise ValueError("Nonadvancing country codes cursor")
            cursors.add(cursor)
        for edge in edges:
            value = edge.get("node", {})
            country, code = value.get("countryCode"), value.get("harmonizedSystemCode")
            if not isinstance(country, str) or not isinstance(code, str) or country in seen:
                raise ValueError("Invalid or duplicate country customs code")
            seen.add(country)
            all_edges.append(edge)
    return {"edges": all_edges}


class FamilyBulkCapture:
    def __init__(self, *, bucket, domain, api_version, shop_gid, extraction_id,
                 family, search_filter, client=None):
        self.bucket, self.client, self.family = bucket, client, family
        self.shop_gid, self.extraction_id = shop_gid, extraction_id
        self.api_version, self.search_filter = api_version, search_filter
        self.sources = {op: (root, (QUERY_DIR / filename).read_text())
                        for op, (root, filename) in FAMILIES[family].items()}
        self.codes_source = (QUERY_DIR / "inventory_country_codes_query.graphql").read_text()
        self.binding = dict(domain=domain, api_version=api_version, shop_gid=shop_gid,
            extraction_id=extraction_id, family=family, search_filter=search_filter,
            queries={op: digest(source.encode()) for op, (_, source) in self.sources.items()})
        if family == "inventory":
            self.binding["country_codes_query_sha256"] = digest(self.codes_source.encode())
        # Deliberately exclude request hash from path: changed windows/projections
        # cannot silently reuse an extraction ID and acquire a second identity.
        key = digest(encoded([shop_gid, family, extraction_id]))
        self.prefix = f"bulk/v2/{family}/{key}"

    def _immutable(self, name, value):
        body = encoded(value)
        blob = self.bucket.blob(name)
        try:
            blob.upload_from_string(body, content_type="application/json", if_generation_match=0)
        except PreconditionFailed:
            blob = self.bucket.get_blob(name)
            if blob.download_as_bytes(if_generation_match=int(blob.generation)) != body:
                raise ValueError("Conflicting bulk capture replay")
        return blob

    def _read(self, ref):
        prefix = f"gs://{self.bucket.name}/"
        if not ref["uri"].startswith(prefix):
            raise ValueError("Bulk file belongs to a different bucket")
        blob = self.bucket.get_blob(ref["uri"][len(prefix):])
        if blob is None or str(blob.generation) != ref["generation"]:
            raise ValueError("Bulk source generation changed")
        if blob.size > 256 * 1024 * 1024:
            raise ValueError("Bulk source exceeds configured size limit")
        body = blob.download_as_bytes(if_generation_match=int(blob.generation))
        if digest(body) != ref["sha256"]:
            raise ValueError("Bulk source checksum changed")
        return body

    def identity(self, operation, file_id, ingested_at):
        root, source = self.sources[operation]
        # Include the capture binding: snapshot windows and supplemental query
        # revisions are part of replay identity even when absent in bulk SQL.
        request = dict(binding=self.binding, document=bind_bulk_query(source, self.search_filter, root=root))
        return ExtractionIdentity(self.shop_gid, self.extraction_id, file_id,
            digest(source.encode()), digest(encoded(request)), self.api_version, ingested_at)

    def _records(self, op, ref, ingested_at):
        return iter_raw_records(io.BytesIO(self._read(ref)), self.identity(op, ref["generation"], ingested_at))

    def _codes(self, item_ids):
        result = {}
        deadline = time.monotonic() + 1200
        for gid in item_ids:
            key = digest(gid.encode())
            saved = self.bucket.get_blob(f"{self.prefix}/codes/{key}.json")
            if saved is not None:
                observed = json.loads(saved.download_as_bytes(if_generation_match=int(saved.generation)))
            else:
                if self.client is None:
                    raise ValueError("Missing inventory country codes capture")
                pages, after, cursors = [], None, set()
                while True:
                    if time.monotonic() >= deadline:
                        raise ValueError("Country codes capture deadline reached; replay saved items")
                    data = self.client._request(self.codes_source, {"id": gid, "after": after})
                    node = data.get("inventoryItem")
                    if not isinstance(node, dict) or node.get("id") != gid:
                        raise ValueError("Country codes owner mismatch")
                    connection = node.get("countryHarmonizedSystemCodes", {})
                    edges, info = connection.get("edges"), connection.get("pageInfo", {})
                    if not isinstance(edges, list) or type(info.get("hasNextPage")) is not bool:
                        raise ValueError("Invalid country codes connection")
                    pages.append(data)
                    if not info["hasNextPage"]:
                        break
                    after = info.get("endCursor")
                    if not edges or not isinstance(after, str) or not after or after in cursors or len(pages) >= 100:
                        raise ValueError("Country codes cursor did not advance")
                    cursors.add(after)
                observed = {"id": gid, "pages": pages}
                saved = self._immutable(f"{self.prefix}/codes/{key}.json", observed)
            if observed.get("id") != gid:
                raise ValueError("Country codes identity mismatch")
            country_codes(observed, gid)
            body = encoded(observed)
            result[gid] = dict(uri=f"gs://{self.bucket.name}/{saved.name}",
                generation=str(saved.generation), sha256=digest(body), role="inventory_country_codes")
        return result

    def collect(self):
        saved = self.bucket.get_blob(self.prefix + "/complete.json")
        if saved is not None:
            seal = json.loads(saved.download_as_bytes(if_generation_match=int(saved.generation)))
            if seal.get("binding") != self.binding:
                raise ValueError("Extraction identity already bound to different bulk capture")
        else:
            if self.client is None:
                raise ValueError("Missing completed bulk capture")
            self._immutable(self.prefix + "/binding.json", self.binding)
            results = {}
            for op, (root, source) in self.sources.items():
                descriptor = self.bucket.get_blob(f"{self.prefix}/{op}.json")
                if descriptor is not None:
                    results[op] = json.loads(descriptor.download_as_bytes(if_generation_match=int(descriptor.generation)))
                    continue
                operation_id = self.client.submit_once(bucket=self.bucket,
                    extraction_id=f"bulk-v2:{self.family}:{op}:{self.extraction_id}",
                    query_source=source, search_filter=self.search_filter, query_root=root)
                export = wait_for_export(self.client, operation_id)
                identity = self.identity(op, "pending", export.completed_at)
                with download_export(export) as source_file:
                    validate_bulk_rows(op, iter_raw_records(source_file, identity),
                        object_count=export.object_count, root_count=export.root_count)
                    source_file.seek(0)
                    landed = land_jsonl(source_file, self.bucket, identity, op)
                ref = dict(landed, role="bulk_jsonl", operation=op,
                    operation_id=operation_id, object_count=export.object_count, root_count=export.root_count,
                    started_at=export.created_at.isoformat(), completed_at=export.completed_at.isoformat())
                results[op] = ref
                self._immutable(f"{self.prefix}/{op}.json", ref)
            codes = {}
            if self.family == "inventory":
                ids = [r["object_gid"] for r in self._records("inventory_items", results["inventory_items"], datetime.now(timezone.utc))]
                codes = self._codes(ids)
            seal = dict(binding=self.binding, exports=results, country_codes=codes,
                        completed_at=datetime.now(timezone.utc).isoformat())
            self._immutable(self.prefix + "/complete.json", seal)
        now = datetime.now(timezone.utc)
        if set(seal["exports"]) != set(self.sources):
            raise ValueError("Incomplete bulk family seal")
        for op, ref in seal["exports"].items():
            validate_bulk_rows(op, self._records(op, ref, now), object_count=ref["object_count"], root_count=ref["root_count"])
        if self.family == "inventory":
            ids = {r["object_gid"] for r in self._records("inventory_items", seal["exports"]["inventory_items"], now)}
            if ids != set(seal["country_codes"]):
                raise ValueError("Incomplete inventory country codes")
            for gid, ref in seal["country_codes"].items():
                country_codes(json.loads(self._read(ref)), gid)
        return seal

    def prepare(self, ingested_at):
        seal = self.collect()
        seal_blob = self.bucket.get_blob(self.prefix + "/complete.json")
        seal_body = seal_blob.download_as_bytes(if_generation_match=int(seal_blob.generation))
        if seal_body != encoded(seal):
            raise ValueError("Bulk completion seal changed during replay")
        seal_ref = dict(uri=f"gs://{self.bucket.name}/{seal_blob.name}",
            generation=str(seal_blob.generation), sha256=digest(seal_body), role="completion_seal")
        streams = {}
        operations = list(self.sources) + (["variants"] if self.family == "catalog" else [])
        for stream in operations:
            op = STREAM_OPERATION.get(stream, stream)
            ref = seal["exports"][op]
            identity = self.identity(op, ref["generation"], ingested_at)
            codes = {}
            if stream == "inventory_items":
                for gid, code_ref in seal["country_codes"].items():
                    data = json.loads(self._read(code_ref))
                    codes[gid] = country_codes(data, gid)
            # Supplemental observations are pinned source files; canonical entity
            # normalization consumes them, while raw JSONL remains unmodified.
            files = [dict(ref, country_codes=codes)] if stream == "inventory_items" else [dict(ref)]
            if stream == "inventory_items":
                files += list(seal["country_codes"].values())
            files.append(dict(seal_ref))
            streams[stream] = dict(records=self._records(op, ref, ingested_at), files=files,
                raw_record_count=ref["record_count"], query_sha256=identity.query_sha256,
                request_sha256=identity.request_sha256, started_at=datetime.fromisoformat(ref["started_at"]),
                completed_at=datetime.fromisoformat(seal["completed_at"]),
                provider_object_count=ref["object_count"], root_object_count=ref["root_count"],
                bulk_operation_id=ref["operation_id"],
                transport=INVENTORY_TRANSPORT if stream == "inventory_items" else TRANSPORT)
        return streams


def validate_family_publication(stream, records, files, manifest):
    refs = [ref for ref in files if ref.get("role") == "bulk_jsonl"]
    if len(refs) != 1:
        raise ValueError("Bulk stream requires one JSONL reference")
    ref = refs[0]
    if (manifest["provider_object_count"] != ref["object_count"]
            or manifest["root_object_count"] != ref["root_count"]
            or manifest["bulk_operation_gid"] != ref["operation_id"]):
        raise ValueError("Bulk manifest differs from completed capture")
    expected_transport = INVENTORY_TRANSPORT if stream == "inventory_items" else TRANSPORT
    if manifest.get("transport") != expected_transport:
        raise ValueError("Unexpected family bulk transport")
    for index, row in enumerate(records, 1):
        if row["file_id"] != ref["generation"] or row["record_index"] != index:
            raise ValueError("Bulk record references another source or physical position")
        body = json.loads(row["record_text"])
        if (digest(row["record_text"].encode()) != row["record_sha256"]
                or row["object_gid"] != body.get("id") or row["parent_gid"] != body.get("__parentId")):
            raise ValueError("Bulk envelope differs from original record")
        for column in ("shop_key", "extraction_id", "query_sha256", "request_sha256"):
            if row[column] != manifest[column]:
                raise ValueError("Bulk row binding differs from manifest")
        if row["api_version"] != manifest["actual_api_version"]:
            raise ValueError("Bulk row API version differs from manifest")
    validate_bulk_rows(stream, records, object_count=ref["object_count"], root_count=ref["root_count"])
    if stream == "inventory_items":
        ids = {r["object_gid"] for r in records}
        if ids != set(ref.get("country_codes", {})):
            raise ValueError("Missing supplemental country codes")
