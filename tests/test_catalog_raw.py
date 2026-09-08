import unittest
from datetime import datetime, timezone
from pathlib import Path
import hashlib

from agent.warehouse.catalog_raw import prepare_catalog_raw
from agent.warehouse.catalog_queries import compile_catalog_queries
from agent.warehouse.raw_publication import _validate_catalog_page_publication
from tests.test_catalog_capture import Blob, Bucket, make, response, connection

ROOT = Path(__file__).parents[1]
CUSTOMERS = (ROOT / "queries/shopify/customers_query.graphql").read_text()
PRODUCTS = (ROOT / "queries/shopify/products_query.graphql").read_text()
VARIANT_QUERY_SHA = hashlib.sha256(
    compile_catalog_queries(CUSTOMERS, PRODUCTS).variants.encode()).hexdigest()


class UniqueGenerationBucket(Bucket):
    def blob(self, name):
        if name not in self.objects:
            self.objects[name] = Blob(name, generation=len(self.objects) + 1)
        return self.objects[name]


class CatalogRawTests(unittest.TestCase):
    def test_streams_are_partitioned_and_empty_variant_keeps_seal(self):
        capture = make(bucket=UniqueGenerationBucket())
        capture.collect()
        prepared = prepare_catalog_raw(
            bucket=capture.bucket, domain="example.myshopify.com",
            api_version="2026-04", shop_gid="gid://shopify/Shop/1",
            extraction_id="catalog-test",
            customer_query=CUSTOMERS,
            product_query=PRODUCTS,
            search_filter="updated_at:>=2026-01-01",
            variant_query_sha256=VARIANT_QUERY_SHA,
            ingested_at=datetime.now(timezone.utc),
        )
        streams = prepared["streams"]
        self.assertEqual(set(streams), {"customers", "products", "variants"})
        self.assertEqual(streams["customers"]["raw_record_count"], 2)
        self.assertEqual(streams["products"]["raw_record_count"], 1)
        self.assertEqual(streams["variants"]["raw_record_count"], 3)
        self.assertTrue(streams["variants"]["files"][-1]["role"] == "completion_seal")
        for operation, result in streams.items():
            _validate_catalog_page_publication(list(result["records"]), result["files"], operation)

    def test_rows_preserve_exact_body_and_page_grain(self):
        capture = make(bucket=UniqueGenerationBucket())
        capture.collect()
        prepared = prepare_catalog_raw(
            bucket=capture.bucket, domain="example.myshopify.com",
            api_version="2026-04", shop_gid="gid://shopify/Shop/1",
            extraction_id="catalog-test",
            customer_query=CUSTOMERS,
            product_query=PRODUCTS,
            search_filter="updated_at:>=2026-01-01",
            variant_query_sha256=VARIANT_QUERY_SHA,
            ingested_at=datetime.now(timezone.utc),
        )
        row = next(prepared["streams"]["customers"]["records"])
        self.assertEqual(row["record_index"], 1)
        self.assertEqual(row["payload"], row["record_text"])
        self.assertEqual(len(row["record_sha256"]), 64)

    def test_zero_products_leaves_variants_seal_only(self):
        pages = {
            ("customers", None, None): response({"customers": connection([], False, None)}),
            ("products", None, None): response({"products": connection([], False, None)}),
        }
        capture = make(bucket=UniqueGenerationBucket(), pages=pages)
        capture.collect()
        prepared = prepare_catalog_raw(
            bucket=capture.bucket, domain="example.myshopify.com",
            api_version="2026-04", shop_gid="gid://shopify/Shop/1",
            extraction_id="catalog-test", customer_query=CUSTOMERS,
            product_query=PRODUCTS, search_filter="updated_at:>=2026-01-01",
            variant_query_sha256=VARIANT_QUERY_SHA,
            ingested_at=datetime.now(timezone.utc),
        )
        variants = prepared["streams"]["variants"]
        self.assertEqual(variants["raw_record_count"], 0)
        self.assertEqual([f["role"] for f in variants["files"]], ["completion_seal"])
        _validate_catalog_page_publication([], variants["files"], "variants")

    def test_variant_hash_is_required_and_not_silently_inferred(self):
        capture = make(bucket=UniqueGenerationBucket())
        capture.collect()
        with self.assertRaisesRegex(ValueError, "variant_query_sha256"):
            prepare_catalog_raw(
                bucket=capture.bucket, domain="example.myshopify.com",
                api_version="2026-04", shop_gid="gid://shopify/Shop/1",
                extraction_id="catalog-test", customer_query=CUSTOMERS,
                product_query=PRODUCTS, search_filter="updated_at:>=2026-01-01",
                ingested_at=datetime.now(timezone.utc),
            )


if __name__ == "__main__":
    unittest.main()
