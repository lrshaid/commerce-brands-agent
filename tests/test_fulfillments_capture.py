import json
import unittest
from datetime import datetime, timezone

from agent.warehouse.fulfillments_capture import FulfillmentsCapture, CaptureError


SOURCE = open("queries/shopify/fulfillments_bulk.graphql").read()


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


class Harness(FulfillmentsCapture):
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


def pages(empty_orders=False, connection_shape=False):
    order1, order2 = "gid://shopify/Order/1", "gid://shopify/Order/2"
    fu1, fu2 = "gid://shopify/Fulfillment/10", "gid://shopify/Fulfillment/20"
    def fulfillments(items):
        if connection_shape:
            return connection(items)
        return items
    out = {
        ("orders", None, None): response({"orders": connection(
            [] if empty_orders else [{"id": order1, "updatedAt": "2026-01-01T00:00:00Z"}],
            not empty_orders, "o1" if not empty_orders else None)}),
        ("orders", None, "o1"): response({"orders": connection([{"id": order2, "updatedAt": "2026-01-02T00:00:00Z"}], False, None)}),
        ("fulfillments", order1, None): response({"node": {"id": order1, "fulfillments": fulfillments(
            [{"id": fu1, "status": "SUCCESS"}])}}),
        ("fulfillments", order2, None): response({"node": {"id": order2, "fulfillments": fulfillments(
            [{"id": fu2}])}}),
    }
    return out


def make(pageset=None, **kwargs):
    return Harness(bucket=kwargs.pop("bucket", Bucket()), domain="example.myshopify.com",
                   token=kwargs.pop("token", "token"), api_version="2026-04",
                   shop_gid="gid://shopify/Shop/1", extraction_id="fulfillments-test",
                   query_source=SOURCE, search_filter="updated_at:>=2026-01-01",
                   pages=pageset if pageset is not None else pages(), **kwargs)


class FulfillmentsCaptureTests(unittest.TestCase):
    def test_capture_seals_with_owner_scoped_fulfillment_pages(self):
        capture = make()
        seal = capture.collect()
        self.assertEqual(seal["counts"], {"orders": 2, "fulfillments": 2})
        self.assertEqual({p["operation"] for p in seal["pages"]}, {"orders", "fulfillments"})
        owner_pages = [p for p in seal["pages"] if p["operation"] == "fulfillments"]
        self.assertEqual({(p["variables"]["id"]) for p in owner_pages},
                         {"gid://shopify/Order/1", "gid://shopify/Order/2"})

    def test_empty_orders_and_empty_fulfillment_list_are_valid(self):
        self.assertEqual(make(pages(empty_orders=True)).collect()["counts"], {"orders": 0, "fulfillments": 0})
        base = pages()
        base[("fulfillments", "gid://shopify/Order/2", None)] = response(
            {"node": {"id": "gid://shopify/Order/2", "fulfillments": []}})
        self.assertEqual(make(base).collect()["counts"], {"orders": 2, "fulfillments": 1})

    def test_connection_shaped_or_missing_payload_fails_closed(self):
        base = pages(connection_shape=True)
        with self.assertRaisesRegex(CaptureError, "list"):
            make(base).collect()
        base = pages()
        base.pop(("fulfillments", "gid://shopify/Order/2", None))
        with self.assertRaises(CaptureError):
            make(base).collect()
        base = pages()
        base[("fulfillments", "gid://shopify/Order/2", None)] = response(
            {"node": {"id": "gid://shopify/Order/999", "fulfillments": []}})
        with self.assertRaisesRegex(CaptureError, "owner"):
            make(base).collect()
        base = pages()
        base[("orders", None, None)] = b"not-json"
        with self.assertRaises(CaptureError):
            make(base).collect()

    def test_fulfillment_cannot_be_owned_by_multiple_orders(self):
        base = pages()
        base[("fulfillments", "gid://shopify/Order/2", None)] = response(
            {"node": {"id": "gid://shopify/Order/2",
                      "fulfillments": [{"id": "gid://shopify/Fulfillment/10"}]}})
        with self.assertRaisesRegex(CaptureError, "multiple orders"):
            make(base).collect()

    def test_read_only_replay_uses_no_http(self):
        capture = make()
        seal = capture.collect()
        replay = Harness(bucket=capture.bucket, domain="example.myshopify.com", token="",
                         api_version="2026-04", shop_gid="gid://shopify/Shop/1",
                         extraction_id="fulfillments-test", query_source=SOURCE,
                         search_filter="updated_at:>=2026-01-01", pages=capture.responses, read_only=True)
        self.assertEqual(replay.collect()["counts"], seal["counts"])
        self.assertEqual(replay.http_calls, [])
        with self.assertRaises(CaptureError):
            make(bucket=Bucket(), read_only=True)


if __name__ == "__main__":
    unittest.main()
