import json
import unittest
from datetime import datetime, timezone

from agent.warehouse.payments_capture import PaymentsCapture, CaptureError


TENDER = open("queries/shopify/tender_transactions_bulk.graphql").read()
BALANCE = open("queries/shopify/balance_transactions_bulk.graphql").read()
DISPUTES = open("queries/shopify/disputes_bulk.graphql").read()


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


class Harness(PaymentsCapture):
    def __init__(self, *args, pages=None, **kwargs):
        self.responses = pages or {}
        self.http_calls = []
        super().__init__(*args, **kwargs)

    def _http(self, document, variables):
        op = next(k for k in self.operations if k in document)
        self.http_calls.append((op, dict(variables)))
        key = (op, variables.get("after"))
        body = self.responses.get(key)
        if body is None:
            raise CaptureError("unexpected request in simulated capture")
        return body


def pages(empty=False):
    tender, balance, dispute = ("gid://shopify/TenderTransaction/1", "gid://shopify/BalanceTransaction/2",
                                "gid://shopify/Dispute/3")
    out = {
        ("tenderTransactions", None): response({"tenderTransactions": connection(
            [] if empty else [{"id": tender, "amount": {"amount": "10.00", "currencyCode": "USD"},
                               "order": {"id": "gid://shopify/Order/1"}}], not empty, "t1" if not empty else None)}),
        ("tenderTransactions", "t1"): response({"tenderTransactions": connection([], False, None)}),
        ("balanceTransactions", None): response({"shopifyPaymentsAccount": {"balanceTransactions": connection(
            [] if empty else [{"id": balance, "type": "CHARGE"}], False, None)}}),
        ("disputes", None): response({"shopifyPaymentsAccount": {"disputes": connection(
            [] if empty else [{"id": dispute, "status": "UNDER_REVIEW"}], not empty, "d1" if not empty else None)}}),
        ("disputes", "d1"): response({"shopifyPaymentsAccount": {"disputes": connection([], False, None)}}),
    }
    return out


def make(pageset=None, **kwargs):
    return Harness(bucket=kwargs.pop("bucket", Bucket()), domain="example.myshopify.com",
                   token=kwargs.pop("token", "token"), api_version="2026-04",
                   shop_gid="gid://shopify/Shop/1", extraction_id="payments-test",
                   tender_source=TENDER, balance_source=BALANCE, disputes_source=DISPUTES,
                   search_filter="created_at:>=2026-01-01", pages=pageset or pages(), **kwargs)


class PaymentsCaptureTests(unittest.TestCase):
    def test_nonempty_paginated_capture_has_seal_and_all_connections(self):
        capture = make()
        seal = capture.collect()
        self.assertEqual(seal["status"], "captured")
        self.assertEqual(seal["counts"], {"tenderTransactions": 1, "balanceTransactions": 1, "disputes": 1})
        self.assertEqual({p["operation"] for p in seal["pages"]},
                         {"tenderTransactions", "balanceTransactions", "disputes"})

    def test_empty_first_pages_still_seal(self):
        capture = make(pages(empty=True))
        self.assertEqual(capture.collect()["counts"],
                         {"tenderTransactions": 0, "balanceTransactions": 0, "disputes": 0})

    def test_missing_account_missing_page_and_duplicate_fail_closed(self):
        base = pages()
        base[("disputes", None)] = response({"shopifyPaymentsAccount": None})
        with self.assertRaisesRegex(CaptureError, "account"):
            make(base).collect()
        base = pages()
        base[("balanceTransactions", None)] = response({"shopifyPaymentsAccount": {}})
        with self.assertRaises(CaptureError):
            make(base).collect()
        base = pages()
        base[("tenderTransactions", None)] = b"not-json"
        with self.assertRaises(CaptureError):
            make(base).collect()
        base = pages()
        base[("tenderTransactions", "t1")] = response({"tenderTransactions": connection(
            [{"id": "gid://shopify/TenderTransaction/1"}], False, None)})
        with self.assertRaisesRegex(CaptureError, "Duplicate"):
            make(base).collect()

    def test_repeated_cursor_fails_closed(self):
        base = pages()
        base[("tenderTransactions", None)] = response({"tenderTransactions": connection(
            [{"id": "gid://shopify/TenderTransaction/1"}], True, "same")})
        base[("tenderTransactions", "same")] = response({"tenderTransactions": connection(
            [{"id": "gid://shopify/TenderTransaction/9"}], True, "same")})
        with self.assertRaises(CaptureError):
            make(base).collect()

    def test_read_only_replay_uses_no_http(self):
        capture = make()
        seal = capture.collect()
        replay = Harness(bucket=capture.bucket, domain="example.myshopify.com", token="",
                         api_version="2026-04", shop_gid="gid://shopify/Shop/1", extraction_id="payments-test",
                         tender_source=TENDER, balance_source=BALANCE, disputes_source=DISPUTES,
                         search_filter="created_at:>=2026-01-01", pages=capture.responses, read_only=True)
        self.assertEqual(replay.collect()["counts"], seal["counts"])
        self.assertEqual(replay.http_calls, [])
        with self.assertRaises(CaptureError):
            make(bucket=Bucket(), read_only=True)


if __name__ == "__main__":
    unittest.main()
