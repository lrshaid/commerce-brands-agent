import json
import unittest
from datetime import datetime, timezone

from agent.warehouse.inventory_capture import InventoryCapture, CaptureError


ITEMS = open("queries/shopify/deprecated/inventory_items_bulk.graphql").read()
LEVELS = open("queries/shopify/deprecated/inventory_levels_bulk.graphql").read()


class Blob:
    def __init__(self, name, body=b"", generation=1, metadata=None):
        self.name, self.body, self.generation = name, body, generation
        self.metadata = metadata or {}
        self.size = len(body)

    def upload_from_string(self, body, content_type=None, if_generation_match=None):
        if if_generation_match == 0 and self.body:
            from google.api_core.exceptions import PreconditionFailed
            raise PreconditionFailed("exists")
        self.body, self.size = body, len(body)

    def download_as_bytes(self, if_generation_match=None):
        if if_generation_match is not None and int(if_generation_match) != int(self.generation):
            raise RuntimeError("generation mismatch")
        return self.body

    def reload(self):
        return None


class Bucket:
    name = "fixture"

    def __init__(self):
        self.objects = {}

    def blob(self, name):
        return self.objects.setdefault(name, Blob(name))

    def get_blob(self, name):
        return self.objects.get(name)


def response(data):
    return json.dumps({"data": data}, separators=(",", ":")).encode()


def connection(nodes, more=False, cursor=None):
    return {"pageInfo": {"hasNextPage": more, "endCursor": cursor},
            "edges": [{"node": n} for n in nodes]}


class Harness(InventoryCapture):
    def __init__(self, *args, pages=None, **kwargs):
        self.responses = pages or {}
        self.http_calls = []
        super().__init__(*args, **kwargs)

    def _http(self, document, variables):
        op = next(k for k in self.operations if k in document)
        self.http_calls.append((op, dict(variables)))
        key = (op, variables.get("id"), variables.get("after"))
        body = self.responses.get(key)
        if body is None:
            raise CaptureError("unexpected request in simulated capture")
        return body


def pages(empty=False):
    item = "gid://shopify/InventoryItem/1"
    location1, location2 = "gid://shopify/Location/1", "gid://shopify/Location/2"
    level1, level2 = "gid://shopify/InventoryLevel/10", "gid://shopify/InventoryLevel/20"
    out = {
        ("inventoryItems", None, None): response({"inventoryItems": connection(
            [] if empty else [{"id": item, "sku": "SKU-1"}], not empty, "i1" if not empty else None)}),
        ("inventoryItems", None, "i1"): response({"inventoryItems": connection([], False, None)}),
        ("locations", None, None): response({"locations": connection(
            [] if empty else [{"id": location1}], not empty, "l1" if not empty else None)}),
        ("locations", None, "l1"): response({"locations": connection([{"id": location2}], False, None)}),
        ("inventoryLevels", location1, None): response({"node": {"id": location1, "inventoryLevels": connection(
            [] if empty else [{"id": level1, "item": {"id": item}, "location": {"id": location1},
                               "quantities": [{"name": "available", "quantity": 5}]}], False, None)}}),
        ("inventoryLevels", location2, None): response({"node": {"id": location2, "inventoryLevels": connection(
            [{"id": level2}], False, None)}}),
    }
    return out


def make(pageset=None, **kwargs):
    return Harness(bucket=kwargs.pop("bucket", Bucket()), domain="example.myshopify.com",
                   token=kwargs.pop("token", "token"), api_version="2026-04",
                   shop_gid="gid://shopify/Shop/1", extraction_id="inventory-test",
                   items_source=ITEMS, levels_source=LEVELS,
                   search_filter="updated_at:>=2026-01-01",
                   pages=pageset if pageset is not None else pages(), **kwargs)


class InventoryCaptureTests(unittest.TestCase):
    def test_capture_seals_with_items_locations_and_owner_pages(self):
        capture = make()
        seal = capture.collect()
        self.assertEqual(seal["counts"], {"inventoryItems": 1, "locations": 2, "inventoryLevels": 2})
        self.assertEqual({p["operation"] for p in seal["pages"]},
                         {"inventoryItems", "locations", "inventoryLevels"})

    def test_empty_scope_still_seals_with_root_pages(self):
        self.assertEqual(make(pages(empty=True)).collect()["counts"],
                         {"inventoryItems": 0, "locations": 0, "inventoryLevels": 0})

    def test_owner_mismatch_missing_page_and_duplicate_fail_closed(self):
        base = pages()
        base[("inventoryLevels", "gid://shopify/Location/1", None)] = response(
            {"node": {"id": "gid://shopify/Location/999", "inventoryLevels": connection([])}})
        with self.assertRaisesRegex(CaptureError, "owner"):
            make(base).collect()
        base = pages()
        base.pop(("inventoryLevels", "gid://shopify/Location/2", None))
        with self.assertRaises(CaptureError):
            make(base).collect()
        base = pages()
        base[("inventoryItems", None, None)] = b"not-json"
        with self.assertRaises(CaptureError):
            make(base).collect()
        base = pages()
        base[("inventoryLevels", "gid://shopify/Location/2", None)] = response(
            {"node": {"id": "gid://shopify/Location/2",
                      "inventoryLevels": connection([{"id": "gid://shopify/InventoryLevel/20"},
                                                     {"id": "gid://shopify/InventoryLevel/20"}])}})
        with self.assertRaisesRegex(CaptureError, "Duplicate"):
            make(base).collect()

    def test_level_cannot_be_owned_by_multiple_locations(self):
        base = pages()
        base[("inventoryLevels", "gid://shopify/Location/2", None)] = response(
            {"node": {"id": "gid://shopify/Location/2",
                      "inventoryLevels": connection([{"id": "gid://shopify/InventoryLevel/10"}])}})
        with self.assertRaisesRegex(CaptureError, "multiple locations"):
            make(base).collect()

    def test_read_only_replay_uses_no_http(self):
        capture = make()
        seal = capture.collect()
        replay = Harness(bucket=capture.bucket, domain="example.myshopify.com", token="",
                         api_version="2026-04", shop_gid="gid://shopify/Shop/1",
                         extraction_id="inventory-test", items_source=ITEMS, levels_source=LEVELS,
                         search_filter="updated_at:>=2026-01-01", pages=capture.responses, read_only=True)
        self.assertEqual(replay.collect()["counts"], seal["counts"])
        self.assertEqual(replay.http_calls, [])
        with self.assertRaises(CaptureError):
            make(bucket=Bucket(), read_only=True)


if __name__ == "__main__":
    unittest.main()
