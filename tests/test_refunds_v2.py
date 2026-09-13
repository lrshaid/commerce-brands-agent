from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests.test_refund_capture import Bucket
from agent.warehouse.refund_capture import CaptureError, digest, encoded
from agent.warehouse.refund_capture_v2 import RefundCaptureV2
from agent.warehouse.refund_raw_v2 import prepare_refund_raw_v2
from agent.warehouse.refund_publication_v2 import validate_refund_publication_v2
from agent.warehouse.refund_queries_v2 import compile_refund_queries_v2, order_transactions_query
from agent.warehouse.order_transactions import validate_order_transactions_file
from agent.warehouse.raw_records import ExtractionIdentity

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "queries/shopify/order_refunds_bulk.graphql").read_text()
STAMP = "2026-09-01T00:00:00+00:00"


def connection(kind="RefundLineItem", count=0, start=0, more=False):
    return {"nodes": [{"id": f"gid://shopify/{kind}/{i}"} for i in range(start, start+count)],
            "pageInfo": {"hasNextPage": more, "endCursor": str(start+count) if count else None}}


def refund(i, returned=None):
    return {"id": f"gid://shopify/Refund/{i}", "__typename": "Refund", "return": returned,
            **{n: connection() for n in ("refundLineItems", "transactions", "orderAdjustments", "refundShippingLines")}}


class RefundV2Tests(unittest.TestCase):
    def fixture(self, refunds=1, returned=None):
        bucket = Bucket()
        args = dict(bucket=bucket, domain="test.myshopify.com", api_version="2026-04",
            shop_gid="gid://shopify/Shop/3", extraction_id="v2-test", query_source=SOURCE,
            search_filter="updated_at:>='2026-09-01' updated_at:<'2026-09-02'")
        capture = RefundCaptureV2(**args, token="test")
        order = {"id": "gid://shopify/Order/1", "updatedAt": STAMP,
                 "refunds": [{"id": f"gid://shopify/Refund/{i}", "createdAt": "2020-01-01T00:00:00Z",
                              "return": returned} for i in range(refunds)]}
        body = (json.dumps(order)+"\n").encode()
        blob = capture._immutable(capture.prefix + "/orders.jsonl", body)
        ref = dict(uri=f"gs://{bucket.name}/{blob.name}", generation=str(blob.generation), sha256=digest(body),
                   role="bulk_headers", operation="orders", captured_at=STAMP, started_at=STAMP,
                   operation_id="gid://shopify/BulkOperation/1", record_count=1, root_count=1)
        capture._immutable(capture.prefix + "/bulk.json", encoded(ref))
        return capture, args

    def test_first_pass_submits_namespaced_bulk_and_persists_original_file(self):
        from contextlib import contextmanager
        capture, args = self.fixture(refunds=0)
        del args["bucket"].objects[capture.prefix + "/bulk.json"]
        body = args["bucket"].objects.pop(capture.prefix + "/orders.jsonl").body

        @contextmanager
        def download(_export, **kwargs):
            yield BytesIO(body)

        export = SimpleNamespace(object_count=1, root_count=1,
            created_at=datetime.fromisoformat(STAMP), completed_at=datetime.fromisoformat(STAMP))
        with patch("agent.warehouse.refund_capture_v2.BulkClient") as client, \
                patch("agent.warehouse.refund_capture_v2.wait_for_export", return_value=export), \
                patch("agent.warehouse.refund_capture_v2.download_export", side_effect=download):
            client.return_value.submit_once.return_value = "gid://shopify/BulkOperation/9"
            seal = capture.collect()
            kwargs = client.return_value.submit_once.call_args.kwargs
            self.assertEqual(kwargs["extraction_id"], "refunds-v2:v2-test")
            self.assertEqual(kwargs["search_filter"], args["search_filter"])
            self.assertNotIn("refundLineItems", kwargs["query_source"])
        self.assertEqual(seal["bulk"]["operation_id"], "gid://shopify/BulkOperation/9")
        self.assertEqual(args["bucket"].objects[capture.prefix + "/orders.jsonl"].body, body)

    def test_batches_topups_money_only_refunds_and_exact_replay(self):
        capture, args = self.fixture(refunds=6)
        nodes = [refund(i) for i in range(6)]
        # A money-only refund, plus a separate shipping-only refund.
        nodes[0]["transactions"] = connection("OrderTransaction", 50, more=True)
        nodes[1]["refundShippingLines"] = connection("RefundShippingLine", 1)
        bodies = [
            encoded({"data": {"nodes": nodes[:5]}}),
            encoded({"data": {"refund": {"id": nodes[0]["id"], "transactions": connection("OrderTransaction", 2, 50)}}}),
            encoded({"data": {"nodes": nodes[5:]}}),
        ]
        with patch.object(capture, "_http", side_effect=bodies) as http:
            seal = capture.collect()
        self.assertEqual(seal["counts"]["transactions"], 52)
        self.assertEqual(seal["counts"]["refundShippingLines"], 1)
        self.assertEqual(seal["counts"]["refundLineItems"], 0)
        self.assertEqual(len(http.call_args_list[0].args[1]["ids"]), 5)
        self.assertEqual(http.call_args_list[1].args[1]["after"], "50")
        before = {k: b.body for k, b in args["bucket"].objects.items()}
        with patch.object(RefundCaptureV2, "_http", side_effect=AssertionError("No network")):
            prepared = prepare_refund_raw_v2(**args, ingested_at=datetime.now(timezone.utc))
            rows = list(prepared["records"])
        validate_refund_publication_v2(rows, prepared["files"])
        self.assertEqual(prepared["raw_record_count"], 4)
        self.assertEqual(rows[1]["record_text"].encode(), bodies[0])
        self.assertEqual(before, {k: b.body for k, b in args["bucket"].objects.items()})
        self.assertIn("2020-01-01", rows[0]["record_text"])  # No refund date exclusion.
        rows.pop()
        with self.assertRaises(ValueError):
            validate_refund_publication_v2(rows, prepared["files"])

    def test_return_children_topup_once_for_shared_return(self):
        capture, _ = self.fixture(refunds=2, returned={"id": "gid://shopify/Return/7"})
        returned = {"id": "gid://shopify/Return/7",
                    "returnLineItems": connection("ReturnLineItem", 50, more=True),
                    "exchangeLineItems": connection("ExchangeLineItem", 1)}
        bodies = [encoded({"data": {"nodes": [refund(0, returned), refund(1, returned)]}}),
                  encoded({"data": {"return": {"id": returned["id"], "returnLineItems": connection("ReturnLineItem", 1, 50)}}})]
        with patch.object(capture, "_http", side_effect=bodies):
            seal = capture.collect()
        self.assertEqual(seal["counts"]["returnLineItems"], 51)
        self.assertEqual(seal["counts"]["exchangeLineItems"], 1)

    def test_partial_null_batch_cannot_seal(self):
        capture, args = self.fixture()
        with patch.object(capture, "_http", return_value=encoded({"data": {"nodes": [None]}})):
            with self.assertRaises(CaptureError):
                capture.collect()
        self.assertNotIn(capture.prefix + "/complete.json", args["bucket"].objects)

    def test_repeated_child_or_wrong_owner_cannot_seal(self):
        for wrong_owner in (False, True):
            capture, args = self.fixture()
            node = refund(0)
            node["refundLineItems"] = connection(count=50, more=True)
            topup = {"id": "gid://shopify/Refund/99" if wrong_owner else node["id"],
                     "refundLineItems": connection(count=1)}
            with patch.object(capture, "_http", side_effect=[
                    encoded({"data": {"nodes": [node]}}), encoded({"data": {"refund": topup}})]):
                with self.assertRaises(CaptureError):
                    capture.collect()
            self.assertNotIn(capture.prefix + "/complete.json", args["bucket"].objects)

    def test_empty_refunds_still_publishes_bulk_and_replays(self):
        capture, args = self.fixture(refunds=0)
        with patch.object(capture, "_http", side_effect=AssertionError("No child request")):
            capture.collect()
        prepared = prepare_refund_raw_v2(**args, ingested_at=datetime.now(timezone.utc))
        self.assertEqual(prepared["raw_record_count"], 1)
        validate_refund_publication_v2(list(prepared["records"]), prepared["files"])

    def test_tampered_bulk_rejected(self):
        capture, args = self.fixture(refunds=0)
        capture.collect()
        args["bucket"].objects[capture.prefix+"/orders.jsonl"].body = b"{}"
        with self.assertRaises(CaptureError):
            prepare_refund_raw_v2(**args, ingested_at=datetime.now(timezone.utc))

    def test_projections_share_transaction_fields(self):
        expected = order_transactions_query(SOURCE)+"\n"
        self.assertEqual((ROOT/"queries/shopify/order_transactions_bulk.graphql").read_text(), expected)
        plan = compile_refund_queries_v2(SOURCE)
        for name in ("refundLineItems", "transactions", "orderAdjustments", "refundShippingLines", "returnLineItems", "exchangeLineItems"):
            self.assertNotIn(name, plan.bulk)
            self.assertIn("after: $after", plan.topups[name])
        self.assertIn("includeRemovedItems: true", plan.topups["exchangeLineItems"])


class OrderTransactionsTests(unittest.TestCase):
    def test_more_than_50_transactions_and_duplicate_rejection(self):
        identity = ExtractionIdentity("shop", "run", "file", "a"*64, "b"*64, "2026-04", datetime.now(timezone.utc))
        row = {"id": "gid://shopify/Order/1", "transactions": [
            {"id": f"gid://shopify/OrderTransaction/{i}", "amountSet": {
                "shopMoney": {"amount": "12.00", "currencyCode": "CAD"},
                "presentmentMoney": {"amount": "9.00", "currencyCode": "USD"}}} for i in range(55)]}
        export = SimpleNamespace(root_count=1, object_count=1)
        result = validate_order_transactions_file(BytesIO(encoded(row)), identity, export)
        self.assertEqual(result["transaction_count"], 55)
        row["transactions"].append(row["transactions"][0])
        with self.assertRaises(CaptureError):
            validate_order_transactions_file(BytesIO(encoded(row)), identity, export)

class RefundCostTests(unittest.TestCase):
    fixture = RefundV2Tests.fixture
    def test_query_cost_reduction_is_replayable_without_failed_requests(self):
        from agent.warehouse.refund_capture_v2 import QueryCostLimit
        capture, args = self.fixture()
        node = refund(0)
        node["transactions"] = connection("OrderTransaction", 25, more=True)
        with patch.object(capture, "_http", side_effect=[
            QueryCostLimit("too large"),
            encoded({"data": {"nodes": [node]}}),
            encoded({"data": {"refund": {"id": node["id"], "transactions": connection("OrderTransaction", 1, 25)}}}),
        ]):
            seal = capture.collect()
        self.assertEqual(seal["pages"][0]["variables"]["first"], 25)
        prepared = prepare_refund_raw_v2(**args, ingested_at=datetime.now(timezone.utc))
        self.assertEqual(prepared["counts"]["transactions"], 26)
