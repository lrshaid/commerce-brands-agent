import unittest
from datetime import datetime, timezone
import hashlib
import json

from agent.warehouse.klaviyo_raw import prepare_klaviyo_raw
from agent.warehouse.raw_publication import _validate_klaviyo_events_page_publication, contract_columns
from tests.test_catalog_capture import Blob, Bucket
from tests.test_klaviyo_capture import METRICS, NEXT, Harness, event, page, profile

BASE = "https://a.klaviyo.com/api/events"
NOW = datetime.now(timezone.utc)


def capture_responses():
    return {
        ("M2", None): page([event("e2", "M2")], next_url=NEXT["M2"], included=[profile("P1")]),
        ("M1", None): page([event("e1", "M1")], included=[profile("P1")]),
        ("M2", NEXT["M2"]): page([], included=[profile("P1")]),
    }


class UniqueGenerationBucket(Bucket):
    def blob(self, name):
        if name not in self.objects:
            self.objects[name] = Blob(name, generation=len(self.objects) + 1)
        return self.objects[name]


def capture_and_prepare(pages=None):
    capture = Harness(bucket=UniqueGenerationBucket(), token="token",
                      account_key="klaviyo-main", extraction_id="klaviyo-raw",
                      metrics=METRICS, window_start="2026-09-09T12:00:00Z",
                      window_end="2026-09-09T13:00:00Z", pages=pages or capture_responses())
    seal = capture.collect()
    prepared = prepare_klaviyo_raw(
        bucket=capture.bucket, token="token", account_key="klaviyo-main",
        extraction_id="klaviyo-raw", metrics=METRICS,
        window_start="2026-09-09T12:00:00Z", window_end="2026-09-09T13:00:00Z",
        ingested_at=NOW)
    return capture, seal, prepared


class KlaviyoRawTests(unittest.TestCase):
    def test_one_row_per_page_with_seal_last_and_valid_grain(self):
        _, seal, prepared = capture_and_prepare()
        events = prepared["streams"]["events"]
        self.assertEqual(set(prepared["streams"]), {"events"})
        self.assertEqual(events["raw_record_count"], 3)
        self.assertEqual(prepared["raw_record_count"], 3)
        self.assertEqual(events["counts"], seal["counts"])
        self.assertEqual([f["role"] for f in events["files"]], ["response_page"] * 3 + ["completion_seal"])
        _validate_klaviyo_events_page_publication(list(events["records"]), events["files"], "events")

    def test_rows_preserve_exact_body_and_envelope(self):
        _, _, prepared = capture_and_prepare()
        row = next(prepared["streams"]["events"]["records"])
        raw, _ = contract_columns()
        self.assertEqual(set(row), set(raw))
        self.assertEqual(row["shop_key"], "klaviyo-main")
        self.assertEqual(row["extraction_id"], "klaviyo-raw")
        self.assertEqual(row["record_index"], 1)
        self.assertEqual(row["api_version"], "2025-07-15")
        self.assertEqual(row["payload"], row["record_text"])
        self.assertEqual(row["record_sha256"], hashlib.sha256(row["record_text"].encode()).hexdigest())

    def test_empty_window_keeps_seal_only_rows(self):
        _, _, prepared = capture_and_prepare(pages={
            ("M2", None): page([], included=[]),
            ("M1", None): page([], included=[]),
        })
        events = prepared["streams"]["events"]
        self.assertEqual(events["raw_record_count"], 2)
        self.assertEqual(events["counts"], {"M2": 0, "M1": 0})
        _validate_klaviyo_events_page_publication(list(events["records"]), events["files"], "events")

    def test_zero_pages_require_the_zero_seal_for_publication(self):
        prepared = {
            "streams": {"events": {"records": iter([]),
                                   "files": [dict(uri="gs://fixture/pages/complete.json",
                                                  generation="9", sha256="a" * 64,
                                                  role="completion_seal",
                                                  klaviyo_counts={"M1": 0})]},
                        "raw_record_count": 0}}
        _validate_klaviyo_events_page_publication(
            list(prepared["streams"]["events"]["records"]),
            prepared["streams"]["events"]["files"], "events")


def publication_fixture():
    raw, _ = contract_columns()
    seal = {"binding": {}, "status": "captured", "counts": {"M1": 1, "M2": 1}}
    texts = {
        101: json.dumps({"data": [event("e1")], "included": [profile("P1")]}, separators=(",", ":")),
        102: json.dumps({"data": [event("e2", "M2", "P1")], "included": [profile("P1")]}, separators=(",", ":")),
        103: json.dumps(seal, separators=(",", ":")),
    }
    files = [
        dict(uri="gs://landing/pages/101.json", generation="101", sha256=hashlib.sha256(texts[101].encode()).hexdigest(),
             request_sha256="b" * 64, operation="M1",
             variables={"page[size]": 200, "sort": "-datetime", "include": "profile",
                        "filter": 'greater-or-equal(datetime,2026-09-09T12:00:00+00:00),less-than(datetime,2026-09-09T13:00:00+00:00),equals(metric_id,"M1")'},
             captured_at="2026-09-09T12:30:00+00:00", role="response_page"),
        dict(uri="gs://landing/pages/102.json", generation="102", sha256=hashlib.sha256(texts[102].encode()).hexdigest(),
             request_sha256="c" * 64, operation="M2",
             variables={"cursor": BASE + "?page%5Bsize%5D=200&cursor=abc"},
             captured_at="2026-09-09T12:30:05+00:00", role="response_page"),
        dict(uri="gs://landing/pages/complete.json", generation="103",
             sha256=hashlib.sha256(texts[103].encode()).hexdigest(), role="completion_seal"),
    ]
    rows = []
    for generation, text in texts.items():
        if generation == 103:
            continue
        row = dict.fromkeys(raw)
        row.update(shop_key="klaviyo-main", extraction_id="klaviyo-1", file_id=str(generation),
                   record_index=1, query_sha256="d" * 64, request_sha256="e" * 64,
                   api_version="2025-07-15", ingested_at="2026-09-09T13:00:00+00:00",
                   record_sha256=hashlib.sha256(text.encode()).hexdigest(),
                   record_text=text, payload=text, object_gid=None, parent_gid=None)
        rows.append(row)
    return rows, files, texts


class KlaviyoPublicationTests(unittest.TestCase):
    def test_page_preflight_accepts_params_and_cursor_pages(self):
        rows, files, _ = publication_fixture()
        _validate_klaviyo_events_page_publication(rows, files)
        _validate_klaviyo_events_page_publication(list(reversed(rows)), files, "events")

    def test_page_preflight_rejects_checksum_and_stream_mismatch(self):
        rows, files, _ = publication_fixture()
        rows[0]["record_sha256"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "checksum"):
            _validate_klaviyo_events_page_publication(rows, files)
        rows, files, _ = publication_fixture()
        with self.assertRaisesRegex(ValueError, "Unknown Klaviyo stream"):
            _validate_klaviyo_events_page_publication(rows, files, "orders")

    def test_page_preflight_rejects_bad_variables_and_cursor(self):
        rows, files, _ = publication_fixture()
        files[0]["variables"]["sort"] = "datetime"
        with self.assertRaisesRegex(ValueError, "metadata"):
            _validate_klaviyo_events_page_publication(rows, files)
        rows, files, _ = publication_fixture()
        files[1]["variables"] = {"cursor": "https://evil.example/api/events?cursor=abc"}
        with self.assertRaisesRegex(ValueError, "cursor"):
            _validate_klaviyo_events_page_publication(rows, files)
        rows, files, _ = publication_fixture()
        files[0]["variables"]["filter"] = 'equals(metric_id,"M9")'
        with self.assertRaisesRegex(ValueError, "metadata"):
            _validate_klaviyo_events_page_publication(rows, files)

    def test_page_preflight_rejects_foreign_metric_and_missing_profile(self):
        rows, files, texts = publication_fixture()
        body = json.loads(texts[101])
        body["data"][0]["relationships"]["metric"]["data"]["id"] = "M9"
        text = json.dumps(body, separators=(",", ":"))
        rows[0]["record_text"] = rows[0]["payload"] = text
        rows[0]["record_sha256"] = files[0]["sha256"] = hashlib.sha256(text.encode()).hexdigest()
        with self.assertRaisesRegex(ValueError, "filtered metric"):
            _validate_klaviyo_events_page_publication(rows, files)
        rows, files, texts = publication_fixture()
        text = json.dumps({"data": [event("e1")], "included": []}, separators=(",", ":"))
        rows[0]["record_text"] = rows[0]["payload"] = text
        rows[0]["record_sha256"] = files[0]["sha256"] = hashlib.sha256(text.encode()).hexdigest()
        with self.assertRaisesRegex(ValueError, "included profiles"):
            _validate_klaviyo_events_page_publication(rows, files)

    def test_page_preflight_rejects_missing_seal_or_bad_empty_count(self):
        rows, files, _ = publication_fixture()
        with self.assertRaisesRegex(ValueError, "completion seal"):
            _validate_klaviyo_events_page_publication(rows, files[:-1])
        empty_files = [dict(uri="gs://landing/complete.json", generation="9",
                            sha256="a" * 64, role="completion_seal", klaviyo_counts={"M1": 1})]
        with self.assertRaisesRegex(ValueError, "zero count"):
            _validate_klaviyo_events_page_publication([], empty_files)


if __name__ == "__main__":
    unittest.main()
