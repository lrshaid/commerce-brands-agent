from datetime import datetime, timezone
import hashlib
import json
import unittest

from agent.warehouse.raw_publication import _validate_catalog_page_publication, contract_columns


NOW = "2026-09-07T00:00:00+00:00"
SHOP = "gid://shopify/Shop/3"
PRODUCT = "gid://shopify/Product/1"


def _catalog_fixture():
    raw, _ = contract_columns()
    files, rows = [], []
    pages = [
        ("101", "customers", {"first": 50, "after": None, "query": "updated_at:>=2026-01-01"},
         {"customers": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": [{"id": "gid://shopify/Customer/1"}]}}),
        ("102", "products", {"first": 50, "after": None, "query": "updated_at:>=2026-01-01"},
         {"products": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": [{"id": PRODUCT}]}}),
        ("103", "variants", {"first": 50, "after": None, "id": PRODUCT},
         {"node": {"id": PRODUCT, "variants": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": []}}}),
    ]
    for generation, operation, variables, data in pages:
        text = json.dumps({"data": data}, separators=(",", ":"))
        sha = hashlib.sha256(text.encode()).hexdigest()
        files.append(dict(uri=f"gs://landing/pages/{generation}.json", generation=generation,
                          sha256=sha, request_sha256="a" * 64, operation=operation,
                          variables=variables, captured_at=NOW, role="response_page"))
        row = dict.fromkeys(raw)
        row.update(shop_key=SHOP, extraction_id="catalog-1", file_id=generation,
                   record_index=1, query_sha256="c" * 64, request_sha256="d" * 64,
                   api_version="2026-04", ingested_at=NOW, record_sha256=sha,
                   record_text=text, payload=text, object_gid=None, parent_gid=None)
        rows.append(row)
    files.append(dict(uri="gs://landing/pages/complete.json", generation="104", sha256="b" * 64,
                      role="completion_seal"))
    return rows, files


class CatalogPublicationTests(unittest.TestCase):
    def test_page_preflight_accepts_all_operations(self):
        rows, files = _catalog_fixture()
        _validate_catalog_page_publication(rows, files)
        _validate_catalog_page_publication(list(reversed(rows)), files)

    def test_page_preflight_rejects_owner_and_checksum(self):
        rows, files = _catalog_fixture()
        files[2]["variables"]["id"] = "gid://shopify/Product/99"
        with self.assertRaisesRegex(ValueError, "owner"):
            _validate_catalog_page_publication(rows, files)
        rows, files = _catalog_fixture()
        rows[1]["record_sha256"] = "e" * 64
        with self.assertRaisesRegex(ValueError, "checksum"):
            _validate_catalog_page_publication(rows, files)

    def test_page_preflight_rejects_stream_operation_mismatch(self):
        rows, files = _catalog_fixture()
        with self.assertRaisesRegex(ValueError, "stream and response"):
            _validate_catalog_page_publication(rows[:1], files, "products")

    def test_page_preflight_rejects_missing_nodes_and_bad_page_info(self):
        rows, files = _catalog_fixture()
        text = json.dumps({"data": {"products": {"pageInfo": {"hasNextPage": False, "endCursor": None}}}}, separators=(",", ":"))
        rows[1]["record_text"] = rows[1]["payload"] = text
        rows[1]["record_sha256"] = files[1]["sha256"] = hashlib.sha256(text.encode()).hexdigest()
        with self.assertRaisesRegex(ValueError, "connection"):
            _validate_catalog_page_publication(rows, files)
        rows, files = _catalog_fixture()
        text = json.dumps({"data": {"products": {"pageInfo": {"hasNextPage": "false", "endCursor": None}, "nodes": []}}}, separators=(",", ":"))
        rows[1]["record_text"] = rows[1]["payload"] = text
        rows[1]["record_sha256"] = files[1]["sha256"] = hashlib.sha256(text.encode()).hexdigest()
        with self.assertRaisesRegex(ValueError, "connection"):
            _validate_catalog_page_publication(rows, files)


if __name__ == "__main__":
    unittest.main()
