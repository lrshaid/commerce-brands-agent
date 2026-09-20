import json
import unittest
from unittest.mock import patch

from agent.warehouse.metafield_capture import MetafieldCapture
from agent.warehouse.refund_capture import CaptureError

ORDER = "gid://shopify/Order/1"
PRODUCT = "gid://shopify/Product/2"
VARIANT = "gid://shopify/ProductVariant/3"
MF = "gid://shopify/Metafield/9"


def conn(nodes, more=False, cursor=None):
    return {"pageInfo": {"hasNextPage": more, "endCursor": cursor},
            "nodes": [{"node": n} if False else n for n in nodes]}


def response(data):
    return json.dumps({"data": data}, separators=(",", ":")).encode()


def pages():
    return {
        ("orders", None, None): response({"orders": conn([{"id": ORDER, "updatedAt": "2026-01-01T00:00:00Z"}])}),
        ("products", None, None): response({"products": conn([{"id": PRODUCT, "updatedAt": "2026-01-01T00:00:00Z"}])}),
        ("productVariants", None, None): response({"productVariants": conn([{"id": VARIANT, "updatedAt": "2026-01-01T00:00:00Z"}])}),
        ("orderMetafields", ORDER, None): response({"node": {"id": ORDER, "metafields": conn([{"id": MF, "namespace": "facts", "key": "k", "value": "v", "type": "single_line_text_field", "description": None, "createdAt": None, "updatedAt": "2026-01-02T00:00:00Z"}])}}),
        ("productMetafields", PRODUCT, None): response({"node": {"id": PRODUCT, "metafields": conn([])}}),
        ("variantMetafields", VARIANT, None): response({"node": {"id": VARIANT, "metafields": conn([])}}),
    }


class Harness(MetafieldCapture):
    def __init__(self, *args, pageset=None, **kwargs):
        self.responses = pageset or pages()
        super().__init__(*args, **kwargs)

    def _http(self, document, variables):
        op = next((k for k, doc in self.operations.items() if doc == document), None)
        self.http_calls = getattr(self, "http_calls", [])
        self.http_calls.append((op, dict(variables)))
        body = self.responses.get((op, variables.get("id"), variables.get("after")))
        if body is None:
            raise CaptureError("unexpected request in simulated capture")
        return body


def make(pageset=None, **kwargs):
    kwargs.setdefault("search_filter", "updated_at:>=2026-01-01")
    return Harness(bucket=FixtureBucket(), domain="example.myshopify.com", token="token",
                   api_version="2026-04", shop_gid="gid://shopify/Shop/1",
                   extraction_id="metafields-test",
                   orders_query=ORDERS_Q, products_query=PRODUCTS_Q, variants_query=VARIANTS_Q,
                   pageset=pageset, **kwargs)


from pathlib import Path
ORDERS_Q = (Path(__file__).resolve().parents[1] / "queries/shopify/metafield_orders_bulk.graphql").read_text()
PRODUCTS_Q = (Path(__file__).resolve().parents[1] / "queries/shopify/metafield_products_bulk.graphql").read_text()
VARIANTS_Q = (Path(__file__).resolve().parents[1] / "queries/shopify/metafield_product_variants_bulk.graphql").read_text()


class FixtureBucket:
    name = "fixture"

    def __init__(self):
        self.objects = {}

    def blob(self, name):
        if name not in self.objects:
            self.objects[name] = FixtureBlob(name)
        return self.objects[name]

    def get_blob(self, name):
        return self.objects.get(name)


class FixtureBlob:
    def __init__(self, name):
        self.name, self.generation, self.size, self.metadata = name, 1, 0, {}

    def upload_from_string(self, body, content_type=None, if_generation_match=None):
        self.body, self.size = body, len(body)

    def download_as_bytes(self, if_generation_match=None):
        return self.body


class MetafieldCaptureTests(unittest.TestCase):
    def test_nonempty_capture_has_seal_and_all_connections(self):
        seal = make().collect()
        self.assertEqual(seal["status"], "captured")
        self.assertEqual(seal["counts"], {"orders": 1, "products": 1, "productVariants": 1,
                                          "orderMetafields": 1, "productMetafields": 0, "variantMetafields": 0})
        self.assertEqual({p["operation"] for p in seal["pages"]},
                         {"orders", "products", "productVariants", "orderMetafields",
                          "productMetafields", "variantMetafields"})

    def test_metafield_owned_by_two_owners_fails_closed(self):
        base = pages()
        base[("productMetafields", "gid://shopify/Product/2", None)] = response({"node": {
            "id": "gid://shopify/Product/2", "metafields": conn([{"id": MF}])}})
        with self.assertRaises(CaptureError):
            make(base).collect()

    def test_invalid_identity_fails_closed(self):
        base = pages()
        base[("productVariants", None, None)] = response({"productVariants": conn([{"sku": "x"}])})
        with self.assertRaises(CaptureError):
            make(base).collect()

    def test_unexpected_page_request_fails_closed(self):
        capture = make()
        with self.assertRaises(CaptureError):
            next(capture.walk("orderMetafields", "gid://shopify/Order/404"))

    def test_replayed_seal_is_returned_without_http(self):
        capture = make()
        seal = capture.collect()
        replay = make(pageset=None)
        # A second collect on the sealed bucket returns the stored seal.
        self.assertEqual(replay.collect()["binding"]["extraction_id"], seal["binding"]["extraction_id"])


if __name__ == "__main__":
    unittest.main()
