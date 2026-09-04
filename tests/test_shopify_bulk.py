import json
from pathlib import Path
from unittest.mock import Mock, patch

import unittest
from google.api_core.exceptions import PreconditionFailed
from graphql import parse, print_ast

from agent.warehouse.shopify_bulk import BulkClient, BulkError, SubmissionUncertain, bind_orders_query

SOURCE = (Path(__file__).parents[1] / "queries/shopify/orders_bulk.graphql").read_text()
OP = "gid://shopify/BulkOperation/123"


class Blob:
    def __init__(self):
        self.generation = None
        self.value = None

    def upload_from_string(self, value, *, content_type, if_generation_match):
        if if_generation_match != (self.generation or 0):
            raise PreconditionFailed("exists")
        self.value = value
        self.generation = (self.generation or 0) + 1

    def reload(self):
        pass

    def download_as_bytes(self, *, if_generation_match):
        assert if_generation_match == self.generation
        return self.value.encode()


def setup():
    client = BulkClient("example.myshopify.com", "sensitive-token")
    blob = Blob()
    bucket = Mock()
    bucket.blob.return_value = blob
    args = dict(bucket=bucket, extraction_id="stable-run", query_source=SOURCE,
                search_filter="updated_at:>=2026-01-01")
    return client, blob, args


def test_ast_binding_preserves_every_selected_field_and_escapes_filter():
    search = 'name:"quoted" \\ newline\n'
    result = parse(bind_orders_query(SOURCE, search))
    original = parse(SOURCE)
    assert not result.definitions[0].variable_definitions
    root = result.definitions[0].selection_set.selections[0]
    assert root.arguments[0].value.value == search
    assert print_ast(root.selection_set) == print_ast(original.definitions[0].selection_set.selections[0].selection_set)


def test_reject_invalid_query():
    for source, search in [
        (SOURCE, ""), ("mutation { orders { id } }", "x"),
        (SOURCE + SOURCE, "x"), (SOURCE.replace("$query", "$other"), "x"),
        (SOURCE.replace("orders(query:", "products(query:"), "x"),
    ]:
        with unittest.TestCase().assertRaises(BulkError):
            bind_orders_query(source, search)


def test_submit_receipt_precedes_request_and_replay_does_not_resubmit():
    client, blob, args = setup()

    def submit(*_):
        assert json.loads(blob.value)["state"] == "submitting"
        return {"bulkOperationRunQuery": {"bulkOperation": {"id": OP}, "userErrors": []}}

    with patch.object(client, "_request", side_effect=submit) as request:
        assert client.submit_once(**args) == OP
        assert client.submit_once(**args) == OP
        assert request.call_count == 1
    assert "sensitive-token" not in blob.value
    assert "updated_at" not in blob.value


def test_uncertain_response_never_resubmits():
    client, blob, args = setup()
    with patch.object(client, "_request", side_effect=RuntimeError("sensitive-token")) as request:
        for _ in range(2):
            with unittest.TestCase().assertRaises(SubmissionUncertain) as error:
                client.submit_once(**args)
            assert "sensitive-token" not in str(error.exception)
        assert request.call_count == 1


def test_changed_request_cannot_reuse_identity():
    client, blob, args = setup()
    with patch.object(client, "_request", return_value={"bulkOperationRunQuery": {
        "bulkOperation": {"id": OP}, "userErrors": []}}):
        client.submit_once(**args)
    args["search_filter"] = "updated_at:>=2026-02-01"
    with unittest.TestCase().assertRaisesRegex(BulkError, "different request"):
        client.submit_once(**args)


def test_transport_error_is_redacted_and_not_retried():
    client, _, _ = setup()
    with patch("agent.warehouse.shopify_bulk.requests.post", side_effect=RuntimeError("sensitive-token")) as post:
        with unittest.TestCase().assertRaises(BulkError) as error:
            client.status(OP)
        assert "sensitive-token" not in str(error.exception)
        assert post.call_count == 1
        assert post.call_args.kwargs["allow_redirects"] is False
    assert "sensitive-token" not in repr(client)


def test_status_requires_exact_identity():
    client, _, _ = setup()
    with patch.object(client, "_request", return_value={"node": {"id": "gid://shopify/BulkOperation/999"}}):
        with unittest.TestCase().assertRaisesRegex(BulkError, "identity mismatch"):
            client.status(OP)


def test_lost_receipt_update_keeps_intent_and_blocks_resubmission():
    client, blob, args = setup()
    original_upload = blob.upload_from_string

    def upload(value, **kwargs):
        if json.loads(value)["state"] == "submitted":
            raise RuntimeError("lost storage response")
        return original_upload(value, **kwargs)

    with patch.object(blob, "upload_from_string", side_effect=upload), patch.object(
        client, "_request", return_value={"bulkOperationRunQuery": {
            "bulkOperation": {"id": OP}, "userErrors": []}}
    ) as request:
        for _ in range(2):
            with unittest.TestCase().assertRaises(SubmissionUncertain):
                client.submit_once(**args)
        assert request.call_count == 1


def test_version_mismatch_rejected_without_exposing_response():
    client, _, _ = setup()
    response = Mock(status_code=200, headers={"X-Shopify-API-Version": "2026-07"})
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    with patch("agent.warehouse.shopify_bulk.requests.post", return_value=response):
        with unittest.TestCase().assertRaisesRegex(BulkError, "version mismatch"):
            client.status(OP)
    response.json.assert_not_called()


def test_authenticated_shop_must_match_domain_and_gid():
    client, _, _ = setup()
    for shop in [{"id": "gid://shopify/Shop/1", "myshopifyDomain": "other.myshopify.com"},
                 {"id": "gid://shopify/Shop/2", "myshopifyDomain": client.shop_domain}]:
        with patch.object(client, "_request", return_value={"shop": shop}):
            with unittest.TestCase().assertRaises(BulkError):
                client.verify_shop("gid://shopify/Shop/1")


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(unittest.FunctionTestCase(value) for name, value in globals().items()
                              if name.startswith("test_") and callable(value))
