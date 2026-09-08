import json
import unittest

from agent.warehouse.catalog_capture import CatalogCapture
from agent.warehouse.refund_capture import CaptureError


CUSTOMERS = open("queries/shopify/customers_query.graphql", encoding="utf-8").read()
PRODUCTS = open("queries/shopify/products_query.graphql", encoding="utf-8").read()


class Blob:
    def __init__(self, name, body=b"", generation=1, metadata=None):
        self.name, self.body, self.generation = name, body, generation
        self.metadata, self.size = metadata or {}, len(body)

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
    return {"pageInfo": {"hasNextPage": more, "endCursor": cursor}, "nodes": nodes}


def edges(nodes, more=False, cursor=None):
    return {"pageInfo": {"hasNextPage": more, "endCursor": cursor}, "nodes": nodes}


def fixture():
    product1 = "gid://shopify/Product/1"
    product2 = "gid://shopify/Product/2"
    variant1 = "gid://shopify/ProductVariant/1"
    variant2 = "gid://shopify/ProductVariant/2"
    variants = lambda variant_id: {"id": variant_id, "sku": "sku", "price": "1.00",
                                   "inventoryQuantity": 1, "inventoryItem": {"id": "gid://shopify/InventoryItem/1"}}
    return {
        ("customers", None, None): response({"customers": connection([{"id": "gid://shopify/Customer/1"}], True, "c1")}),
        ("customers", None, "c1"): response({"customers": connection([], False, None)}),
        ("products", None, None): response({"products": connection([{"id": product1}, {"id": product2}], False, None)}),
        ("variants", product1, None): response({"node": {"id": product1, "variants": edges([variants(variant1)], True, "v1")}}),
        ("variants", product1, "v1"): response({"node": {"id": product1, "variants": edges([variants(variant2)], False, None)}}),
        ("variants", product2, None): response({"node": {"id": product2, "variants": edges([], False, None)}}),
    }


class Harness(CatalogCapture):
    def __init__(self, *args, pages=None, **kwargs):
        self.responses = pages or fixture()
        self.http_calls = []
        super().__init__(*args, **kwargs)

    def _http(self, document, variables):
        operation = "variants" if "CatalogVariantsPage" in document else ("customers" if "CustomersSnapshot" in document else "products")
        key = (operation, variables.get("id"), variables.get("after"))
        self.http_calls.append(key)
        if key not in self.responses:
            raise CaptureError("unexpected request in simulated capture")
        return self.responses[key]


def make(bucket=None, pages=None, read_only=False):
    return Harness(bucket=bucket or Bucket(), domain="example.myshopify.com", token="token" if not read_only else "",
                   api_version="2026-04", shop_gid="gid://shopify/Shop/1", extraction_id="catalog-test",
                   customer_query=CUSTOMERS, product_query=PRODUCTS, search_filter="updated_at:>=2026-01-01",
                   pages=pages, read_only=read_only)


class CatalogCaptureTests(unittest.TestCase):
    def test_multipage_root_and_variants_with_empty_variant_owner(self):
        capture = make()
        seal = capture.collect()
        self.assertEqual(seal["counts"], {"customers": 1, "products": 2, "variants": 2})
        self.assertEqual({p["operation"] for p in seal["pages"]}, {"customers", "products", "variants"})

    def test_owner_mismatch_rejected(self):
        pages = fixture()
        pages[("variants", "gid://shopify/Product/1", None)] = response({"node": {"id": "gid://shopify/Product/9", "variants": edges([], False)}})
        with self.assertRaises(CaptureError):
            make(pages=pages).collect()

    def test_duplicate_id_and_repeated_cursor_rejected(self):
        pages = fixture()
        pages[("products", None, None)] = response({"products": connection([{"id": "gid://shopify/Product/1"}], True, "p1")})
        pages[("products", None, "p1")] = response({"products": connection([{"id": "gid://shopify/Product/1"}], False, None)})
        with self.assertRaises(CaptureError):
            make(pages=pages).collect()

    def test_variant_cannot_be_owned_by_two_products(self):
        pages = fixture()
        shared = {"id": "gid://shopify/ProductVariant/1", "sku": "sku", "price": "1.00",
                  "inventoryQuantity": 1, "inventoryItem": {"id": "gid://shopify/InventoryItem/1"}}
        pages[("variants", "gid://shopify/Product/2", None)] = response({
            "node": {"id": "gid://shopify/Product/2", "variants": edges([shared], False, None)}})
        with self.assertRaisesRegex(CaptureError, "multiple products"):
            make(pages=pages).collect()
        pages = fixture()
        pages[("customers", None, "c1")] = response({"customers": connection([], True, "c1")})
        with self.assertRaises(CaptureError):
            make(pages=pages).collect()

    def test_read_only_replay_no_http_and_bad_hash_rejected(self):
        capture = make()
        seal = capture.collect()
        replay = make(bucket=capture.bucket, read_only=True)
        self.assertEqual(replay.collect()["counts"], seal["counts"])
        self.assertEqual(replay.http_calls, [])
        page = next(page for page in seal["pages"] if page["operation"] == "products")
        capture.bucket.objects[page["uri"].split("fixture/", 1)[1]].metadata["response_sha256"] = "bad"
        with self.assertRaises(CaptureError):
            make(bucket=capture.bucket, read_only=True).collect()


if __name__ == "__main__":
    unittest.main()
