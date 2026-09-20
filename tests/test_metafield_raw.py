import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from tests.test_metafield_capture import FixtureBucket, make
from agent.warehouse.metafield_raw import prepare_metafield_raw
from agent.warehouse.refund_capture import CaptureError

ORDERS_Q = (Path(__file__).resolve().parents[1] / "queries/shopify/metafield_orders_bulk.graphql").read_text()
PRODUCTS_Q = (Path(__file__).resolve().parents[1] / "queries/shopify/metafield_products_bulk.graphql").read_text()
VARIANTS_Q = (Path(__file__).resolve().parents[1] / "queries/shopify/metafield_product_variants_bulk.graphql").read_text()


class MetafieldRawTests(unittest.TestCase):
    def args(self, bucket):
        return dict(bucket=bucket, domain="example.myshopify.com", api_version="2026-04",
                    shop_gid="gid://shopify/Shop/1", extraction_id="metafields-test",
                    orders_query=ORDERS_Q, products_query=PRODUCTS_Q, variants_query=VARIANTS_Q,
                    search_filter="updated_at:>=2026-01-01",
                    ingested_at=datetime.now(timezone.utc), page_size=50)

    def test_streams_carry_only_metafield_pages(self):
        capture = make()
        seal = capture.collect()
        bucket = capture.bucket
        plan_page_hashes = seal["binding"]
        prepared = prepare_metafield_raw(**self.args(bucket),
            page_query_sha256={s: plan_page_hashes[f"{s}_query_sha256"]
                               for s in ("metafield_orders", "metafield_products",
                                         "metafield_product_variants")})
        self.assertEqual(set(prepared["streams"]),
                         {"metafield_orders", "metafield_products", "metafield_product_variants"})
        self.assertEqual(prepared["counts"]["orderMetafields"], 1)
        self.assertEqual(prepared["counts"]["productMetafields"], 0)
        rows = list(prepared["streams"]["metafield_orders"]["records"])
        self.assertEqual(len(rows), 1)
        body = json.loads(rows[0]["record_text"])
        self.assertEqual(body["data"]["node"]["metafields"]["nodes"][0]["id"],
                         "gid://shopify/Metafield/9")
        # Page-grain transport: one record per captured metafield page, even
        # when the page carries zero metafields.
        self.assertEqual(prepared["streams"]["metafield_orders"]["raw_record_count"], 1)
        self.assertEqual(prepared["streams"]["metafield_products"]["raw_record_count"], 1)
        self.assertEqual(prepared["streams"]["metafield_product_variants"]["raw_record_count"], 1)
        self.assertEqual(sum(f["role"] == "completion_seal" for f in prepared["streams"]["metafield_orders"]["files"]), 1)

    def test_mismatched_page_hash_is_rejected(self):
        with self.assertRaises(ValueError):
            prepare_metafield_raw(**self.args(FixtureBucket()), page_query_sha256={})

    def test_missing_seal_fails_closed(self):
        capture = make()
        capture.collect()
        bucket = capture.bucket
        for name in list(bucket.objects):
            if name.endswith("complete.json"):
                del bucket.objects[name]
        args = self.args(bucket)
        args["page_query_sha256"] = {s: "x" for s in
                                     ("metafield_orders", "metafield_products", "metafield_product_variants")}
        with self.assertRaises(ValueError):
            prepare_metafield_raw(**args)


if __name__ == "__main__":
    unittest.main()
