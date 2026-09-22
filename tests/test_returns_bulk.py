import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
import unittest

from agent.warehouse.entity_contract import contracts_for_stream, load_entity_contract
from agent.warehouse.raw_records import ExtractionIdentity
from agent.warehouse.returns_bulk_entities import iter_returns_entities, validate_returns_file
from agent.warehouse.returns_publication_v2 import validate_returns_publication_v2
from agent.warehouse.shopify_bulk import BulkError

ORDER = "gid://shopify/Order/1"
ORDER2 = "gid://shopify/Order/2"
RETURN = "gid://shopify/Return/10"
LINE = "gid://shopify/ReturnLineItem/100"
EXCHANGE = "gid://shopify/ExchangeLineItem/200"


@dataclass
class Export:
    object_count: int
    root_count: int


def lines():
    return [
        {"__typename": "Order", "id": ORDER, "updatedAt": "2024-01-05T00:00:00Z"},
        {"__typename": "Return", "id": RETURN, "__parentId": ORDER, "name": "Ret#1",
         "status": "OPEN", "totalQuantity": 1},
        {"__typename": "ReturnLineItem", "id": LINE, "__parentId": RETURN, "quantity": 1,
         "fulfillmentLineItem": {"id": "gid://shopify/FulfillmentLineItem/7",
                                 "lineItem": {"id": "gid://shopify/LineItem/77"}}},
        {"__typename": "ExchangeLineItem", "id": EXCHANGE, "__parentId": RETURN, "quantity": 1},
        {"__typename": "Order", "id": ORDER2, "updatedAt": "2024-02-05T00:00:00Z"},
    ]


def body():
    return ("\n".join(json.dumps(line) for line in lines()) + "\n").encode()


def identity():
    return ExtractionIdentity("gid://shopify/Shop/3", "returns-entity-bf-2024-01-03", "gen-1",
                              "a" * 64, "b" * 64, "2026-04",
                              datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc))


def files_record_count(n):
    return [{"uri": "gs://fixture/prefix/bulk.jsonl", "generation": "gen-1",
             "sha256": hashlib.sha256(body()).hexdigest(), "role": "bulk_returns",
             "record_count": str(n)},
            {"uri": "gs://fixture/prefix/complete.json", "generation": "gen-2",
             "sha256": "s" * 64, "role": "completion_seal", "record_count": "0"}]


class ValidateReturnsFileTests(unittest.TestCase):
    def test_valid_counts_and_parents(self):
        source = BytesIO(body())
        result = validate_returns_file(source, identity(), Export(5, 2))
        self.assertEqual(result, {"record_count": 5, "root_count": 2})

    def test_wrong_root_count_fails(self):
        source = BytesIO(body())
        with self.assertRaises(BulkError):
            validate_returns_file(source, identity(), Export(5, 3))

    def test_duplicate_identity_fails(self):
        doubled = body() + body().splitlines()[0:1][0] + b"\n"
        source = BytesIO(doubled)
        with self.assertRaises(BulkError):
            validate_returns_file(source, identity(), Export(6, 3))


class IterReturnsEntitiesTests(unittest.TestCase):
    def contracts(self):
        return contracts_for_stream(load_entity_contract(), "returns").entities

    def test_exchanges_reattached_and_lines_resolved(self):
        rows = list(iter_returns_entities(BytesIO(body()), identity(),
                                          datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc),
                                          self.contracts()))
        entities = [row.entity for row in rows]
        self.assertEqual(entities, ["returns", "return_line_items"])
        returns_row = rows[0].values
        self.assertEqual(returns_row["return_gid"], RETURN)
        self.assertEqual(returns_row["order_gid"], ORDER)
        self.assertEqual(returns_row["source_updated_at"], datetime(2024, 1, 5, 0, 0, tzinfo=timezone.utc))
        exchanges = json.loads(returns_row["exchange_line_items"])
        self.assertEqual([n["id"] for n in exchanges["nodes"]], [EXCHANGE])
        line_row = rows[1].values
        self.assertEqual(line_row["return_gid"], RETURN)
        self.assertEqual(line_row["order_gid"], ORDER)
        self.assertEqual(line_row["order_line_item_gid"], "gid://shopify/LineItem/77")


class ValidateReturnsPublicationV2Tests(unittest.TestCase):
    def _rows(self):
        rows = []
        for index, line in enumerate(lines(), start=1):
            text = json.dumps(line)
            rows.append(dict(shop_key="gid://shopify/Shop/3", extraction_id="x",
                             file_id="gen-1", record_index=index,
                             query_sha256="q" * 64, request_sha256="r" * 64,
                             api_version="2026-04",
                             ingested_at="2026-09-21T12:00:00+00:00",
                             record_sha256=hashlib.sha256(text.encode()).hexdigest(),
                             record_text=text, payload=text,
                             object_gid=line["id"], parent_gid=line.get("__parentId")))
        return rows

    def test_full_coverage_passes(self):
        validate_returns_publication_v2(self._rows(), files_record_count(5))

    def test_missing_line_fails(self):
        with self.assertRaises(ValueError):
            validate_returns_publication_v2(self._rows()[:-1], files_record_count(5))
