from datetime import datetime, timezone
from pathlib import Path
import unittest
from unittest.mock import patch

from agent.warehouse.inventory_capture import InventoryCapture
from agent.warehouse.inventory_raw import prepare_inventory_raw
from agent.warehouse.refund_capture import CaptureError
from tests.test_inventory_capture import Blob, Bucket, connection, response

ROOT = Path(__file__).resolve().parents[1]
ITEMS = (ROOT / "queries/shopify/inventory_items_bulk.graphql").read_text()
LEVELS = (ROOT / "queries/shopify/inventory_levels_bulk.graphql").read_text()


class UniqueGenerationBucket(Bucket):
    def blob(self, name):
        if name not in self.objects:
            self.objects[name] = Blob(name, generation=len(self.objects) + 1)
        return self.objects[name]


class InventoryRawTests(unittest.TestCase):
    def fixture(self, pageset=None):
        bucket = UniqueGenerationBucket()
        args = dict(bucket=bucket, domain="test.myshopify.com", api_version="2026-04",
                    shop_gid="gid://shopify/Shop/3", extraction_id="same-run",
                    items_source=ITEMS, levels_source=LEVELS,
                    search_filter="updated_at:>=2025-01-01", page_size=2)
        capture = InventoryCapture(**args, token="private-token")
        with patch.object(capture, "_http", side_effect=[
                response({"inventoryItems": connection([{"id": "gid://shopify/InventoryItem/1"}], False, None)}),
                response({"locations": connection([{"id": "gid://shopify/Location/1"}], False, None)}),
                response({"node": {"id": "gid://shopify/Location/1", "inventoryLevels": connection(
                    [{"id": "gid://shopify/InventoryLevel/10",
                      "item": {"id": "gid://shopify/InventoryItem/1"},
                      "location": {"id": "gid://shopify/Location/1"},
                      "quantities": [{"name": "available", "quantity": 5}]}], False, None)}})]):
            seal = capture.collect()
        return args, capture, seal

    def test_streams_are_partitioned_and_replayed_exactly(self):
        args, capture, seal = self.fixture()
        before = {key: (obj.generation, obj.body) for key, obj in args["bucket"].objects.items()}
        with patch.object(InventoryCapture, "_http", side_effect=AssertionError("No HTTP")):
            prepared = prepare_inventory_raw(**args, ingested_at=datetime.now(timezone.utc))
            self.assertEqual(set(prepared["streams"]), {"inventory_items", "inventory_levels"})
            items = list(prepared["streams"]["inventory_items"]["records"])
            levels = list(prepared["streams"]["inventory_levels"]["records"])
        self.assertEqual(prepared["raw_record_count"], 3)
        self.assertEqual(len(items), 1)
        self.assertEqual(len(levels), 2)
        for row in (*items, *levels):
            self.assertEqual(row["payload"], row["record_text"])
            self.assertEqual(row["record_index"], 1)
        self.assertEqual(prepared["counts"],
                         {"inventoryItems": 1, "locations": 1, "inventoryLevels": 1})
        self.assertEqual(before, {key: (obj.generation, obj.body) for key, obj in args["bucket"].objects.items()})

    def test_empty_level_pages_keep_their_seal(self):
        args, capture, seal = self.fixture()
        args["bucket"] = UniqueGenerationBucket()
        capture2 = InventoryCapture(**args, token="private-token")
        with patch.object(capture2, "_http", side_effect=[
                response({"inventoryItems": connection([], False, None)}),
                response({"locations": connection([], False, None)})]):
            capture2.collect()
        with patch.object(InventoryCapture, "_http", side_effect=AssertionError("No HTTP")):
            prepared = prepare_inventory_raw(**args, ingested_at=datetime.now(timezone.utc))
        self.assertEqual(prepared["streams"]["inventory_levels"]["raw_record_count"], 1)
        self.assertEqual(len(prepared["streams"]["inventory_levels"]["files"]), 2)

    def test_missing_page_is_not_refetched_in_read_only_mode(self):
        args, capture, seal = self.fixture()
        page_name = seal["pages"][0]["uri"].removeprefix("gs://fixture/")
        del args["bucket"].objects[page_name]
        with patch.object(InventoryCapture, "_http", side_effect=AssertionError("No HTTP")), \
                self.assertRaises(CaptureError):
            prepare_inventory_raw(**args, ingested_at=datetime.now(timezone.utc))


if __name__ == "__main__":
    unittest.main()
