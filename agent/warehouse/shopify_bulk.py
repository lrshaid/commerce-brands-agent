"""Restricted Shopify export control; never retries an uncertain submission.

Receipts live in GCS, independently of the ephemeral Cloud Run task. A receipt
without an operation ID requires operator reconciliation, not another export.
No download or raw publication is performed by this module.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

import requests
from google.api_core.exceptions import PreconditionFailed
from graphql import parse, print_ast, visit, Visitor
from graphql.language.ast import OperationDefinitionNode, StringValueNode
from graphql.language import OperationType

START_EXPORT = """mutation StartOrdersExport($document: String!) {
  bulkOperationRunQuery(query: $document) {
    bulkOperation { id status }
    userErrors { field message }
  }
}"""
EXPORT_STATUS = """query ExportStatus($id: ID!) {
  node(id: $id) {
    ... on BulkOperation {
      id status objectCount rootObjectCount fileSize url partialDataUrl
      errorCode createdAt completedAt
    }
  }
}"""
SHOP_IDENTITY = """query ExportShopIdentity { shop { id myshopifyDomain } }"""


class BulkError(RuntimeError):
    """Sanitized error: never include response bodies, tokens or signed URLs."""


class SubmissionUncertain(BulkError):
    pass


def bind_orders_query(source: str, search_filter: str) -> str:
    """Bind the existing query using the AST, preserving its full projection."""
    if not isinstance(search_filter, str) or not search_filter.strip():
        raise BulkError("An explicit orders search filter is required")
    try:
        document = parse(source)
    except Exception:
        raise BulkError("Invalid orders query") from None
    if len(document.definitions) != 1:
        raise BulkError("Expected exactly one orders query")
    operation = document.definitions[0]
    if not isinstance(operation, OperationDefinitionNode) or operation.operation != OperationType.QUERY:
        raise BulkError("Only an orders query is allowed")
    roots = operation.selection_set.selections
    if len(roots) != 1 or getattr(getattr(roots[0], "name", None), "value", None) != "orders":
        raise BulkError("Expected orders as the only root")
    if len(operation.variable_definitions) != 1 or operation.variable_definitions[0].variable.name.value != "query":
        raise BulkError("Expected only the query variable")
    operation.variable_definitions = ()

    class Bind(Visitor):
        def enter_variable(self, node, *_):
            if node.name.value != "query":
                raise BulkError("Unexpected variable")
            return StringValueNode(value=search_filter)

    return print_ast(visit(document, Bind()))


def _operation_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"gid://shopify/BulkOperation/[0-9]+", value):
        raise BulkError("Invalid Bulk operation identity")
    return value


@dataclass(repr=False)
class BulkClient:
    shop_domain: str
    token: str = field(repr=False)
    api_version: str = "2026-04"

    def __post_init__(self):
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.myshopify\.com", self.shop_domain):
            raise BulkError("Invalid shop domain")
        if not re.fullmatch(r"20[0-9]{2}-(01|04|07|10)", self.api_version):
            raise BulkError("Invalid API version")
        self.token = self.token.strip()
        if not self.token:
            raise BulkError("Missing Shopify credential")

    def _request(self, operation, variables):
        try:
            response = requests.post(
                f"https://{self.shop_domain}/admin/api/{self.api_version}/graphql.json",
                headers={"X-Shopify-Access-Token": self.token},
                json={"query": operation, "variables": variables},
                timeout=(10, 30), allow_redirects=False,
            )
            with response:
                if response.status_code != 200:
                    raise BulkError("Shopify HTTP request failed")
                if response.headers.get("X-Shopify-API-Version") != self.api_version:
                    raise BulkError("Shopify API version mismatch")
                body = response.json()
            if not isinstance(body, dict) or body.get("errors") or not isinstance(body.get("data"), dict):
                raise BulkError("Shopify GraphQL request failed")
            return body["data"]
        except BulkError:
            raise
        except Exception:
            raise BulkError("Shopify transport or response failure") from None

    def status(self, operation_id):
        operation_id = _operation_id(operation_id)
        result = self._request(EXPORT_STATUS, {"id": operation_id}).get("node")
        if not isinstance(result, dict) or result.get("id") != operation_id:
            raise BulkError("Bulk operation not found or identity mismatch")
        return result  # Contains a signed URL: caller must not log this object.

    def verify_shop(self, expected_shop_gid):
        if not isinstance(expected_shop_gid, str) or not re.fullmatch(r"gid://shopify/Shop/[0-9]+", expected_shop_gid):
            raise BulkError("An expected shop GID is required")
        shop = self._request(SHOP_IDENTITY, {}).get("shop")
        if (not isinstance(shop, dict) or shop.get("id") != expected_shop_gid
                or shop.get("myshopifyDomain") != self.shop_domain):
            raise BulkError("Authenticated shop does not match configured identity")
        return expected_shop_gid

    def submit_once(self, *, bucket, extraction_id: str, query_source: str, search_filter: str):
        """Create a durable intent before submission; resume only a saved exact ID.

        The same extraction_id must be retained on retries. GCS generation
        preconditions also prevent two workers from submitting it concurrently.
        """
        if not extraction_id or not isinstance(extraction_id, str):
            raise BulkError("An extraction identity is required")
        document = bind_orders_query(query_source, search_filter)
        binding = {
            "shop_domain": self.shop_domain, "api_version": self.api_version,
            "extraction_id": extraction_id,
            "query_sha256": hashlib.sha256(query_source.encode()).hexdigest(),
            "request_sha256": hashlib.sha256(document.encode()).hexdigest(),
        }
        key = hashlib.sha256(json.dumps([self.shop_domain, extraction_id]).encode()).hexdigest()
        receipt = bucket.blob(f"control/shopify/orders/{key}.json")
        try:
            receipt.upload_from_string(json.dumps({"binding": binding, "state": "submitting"}),
                                       content_type="application/json", if_generation_match=0)
        except PreconditionFailed:
            receipt.reload()
            existing = json.loads(receipt.download_as_bytes(if_generation_match=receipt.generation))
            if existing.get("binding") != binding:
                raise BulkError("Extraction identity already bound to a different request")
            if existing.get("state") == "submitted":
                return _operation_id(existing.get("operation_id"))
            raise SubmissionUncertain("Existing export intent requires reconciliation; not resubmitted")
        # Any failure from here is uncertain: Shopify may have accepted the export.
        try:
            result = self._request(START_EXPORT, {"document": document}).get("bulkOperationRunQuery")
            if not isinstance(result, dict) or result.get("userErrors"):
                raise BulkError("Export submission rejected")
            operation_id = _operation_id((result.get("bulkOperation") or {}).get("id"))
            receipt.upload_from_string(json.dumps({"binding": binding, "state": "submitted",
                                                  "operation_id": operation_id}),
                                       content_type="application/json",
                                       if_generation_match=receipt.generation)
            return operation_id
        except Exception:
            raise SubmissionUncertain("Export submission requires reconciliation; not safe to resubmit") from None
