import unittest
from datetime import datetime, timezone
import hashlib
import json

from agent.warehouse.klaviyo_raw import CONTRACT, prepare_klaviyo_raw
from agent.warehouse.raw_publication import _validate_klaviyo_events_page_publication
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
        ingested_at=NOW, published_at=NOW)
    return capture, seal, prepared


class KlaviyoRawTests(unittest.TestCase):
    def test_one_row_per_event_with_seal_last_and_valid_grain(self):
        _, seal, prepared = capture_and_prepare()
        events = prepared["streams"]["events"]
        records = list(events["records"])
        self.assertEqual(set(prepared["streams"]), {"events"})
        self.assertEqual(sum(seal["counts"].values()), 2)
        self.assertEqual(events["raw_record_count"], 2)
        self.assertEqual(prepared["raw_record_count"], 2)
        self.assertEqual(events["counts"], seal["counts"])
        self.assertEqual([f["role"] for f in events["files"]], ["response_page"] * 3 + ["completion_seal"])
        _validate_klaviyo_events_page_publication(records, events["files"], "events")

    def test_rows_flatten_the_contract_columns_from_the_verbatim_event(self):
        _, _, prepared = capture_and_prepare()
        row = next(prepared["streams"]["events"]["records"])
        self.assertEqual(set(row), {column.name for column in CONTRACT.columns})
        self.assertEqual(row["shop_key"], "klaviyo-main")
        self.assertEqual(row["event_gid"], "e2")
        self.assertEqual(row["uuid"], "uuid-e2")
        self.assertEqual(row["metric_id"], "M2")
        self.assertEqual(row["profile_gid"], "P1")
        self.assertEqual(row["email"], "person@example.com")
        self.assertEqual(row["event_datetime"], "2026-09-09T12:10:00+00:00")
        self.assertEqual(row["event_timestamp"], str(1757419800))
        self.assertEqual(row["source_extraction_id"], "klaviyo-raw")
        payload = json.loads(row["original_payload"])
        self.assertEqual(payload["id"], "e2")
        self.assertEqual(payload["attributes"]["uuid"], "uuid-e2")
        # The flattened values must be derivable from the verbatim payload.
        self.assertEqual(payload["attributes"]["event_properties"], {})

    def test_empty_window_keeps_seal_only_rows(self):
        _, _, prepared = capture_and_prepare(pages={
            ("M2", None): page([], included=[]),
            ("M1", None): page([], included=[]),
        })
        events = prepared["streams"]["events"]
        self.assertEqual(events["raw_record_count"], 0)
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
    seal = {"binding": {}, "status": "captured", "counts": {"M1": 1, "M2": 1}}
    events = [event("e1"), event("e2", "M2")]
    profiles = [profile("P1")]
    pages = {
        101: json.dumps({"data": [events[0]], "included": profiles}, separators=(",", ":")),
        102: json.dumps({"data": [events[1]], "included": profiles}, separators=(",", ":")),
        103: json.dumps(seal, separators=(",", ":")),
    }
    texts = {generation: json.dumps(event_obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
             for generation, event_obj in zip((101, 102), events)}
    texts[103] = json.dumps(seal, separators=(",", ":"))
    files = [
        dict(uri="gs://landing/pages/101.json", generation="101", sha256=hashlib.sha256(pages[101].encode()).hexdigest(),
             request_sha256="b" * 64, operation="M1",
             variables={"page[size]": 200, "sort": "-datetime", "include": "profile",
                        "filter": 'greater-or-equal(datetime,2026-09-09T12:00:00+00:00),less-than(datetime,2026-09-09T13:00:00+00:00),equals(metric_id,"M1")'},
             captured_at="2026-09-09T12:30:00+00:00", role="response_page"),
        dict(uri="gs://landing/pages/102.json", generation="102", sha256=hashlib.sha256(pages[102].encode()).hexdigest(),
             request_sha256="c" * 64, operation="M2",
             variables={"cursor": BASE + "?page%5Bsize%5D=200&cursor=abc"},
             captured_at="2026-09-09T12:30:05+00:00", role="response_page"),
        dict(uri="gs://landing/pages/complete.json", generation="103",
             sha256=hashlib.sha256(texts[103].encode()).hexdigest(), role="completion_seal",
             klaviyo_counts={"M1": 1, "M2": 1}),
    ]
    rows = []
    for generation, event_obj in zip((101, 102), events):
        row = {column.name: None for column in CONTRACT.columns}
        row.update(shop_key="klaviyo-main", event_gid=event_obj["id"],
                   uuid=event_obj["attributes"]["uuid"], metric_id=event_obj["relationships"]["metric"]["data"]["id"],
                   profile_gid="P1", email="person@example.com",
                   event_datetime=event_obj["attributes"]["datetime"],
                   event_timestamp=str(event_obj["attributes"]["timestamp"]),
                   original_payload=texts[generation],
                   source_extraction_id="klaviyo-1", source_published_at="2026-09-09T13:00:00+00:00",
                   ingested_at="2026-09-09T13:00:00+00:00")
        rows.append(row)
    return rows, files, texts, events


class KlaviyoPublicationTests(unittest.TestCase):
    def test_event_preflight_accepts_first_and_cursor_page_rows(self):
        rows, files, _, _ = publication_fixture()
        _validate_klaviyo_events_page_publication(rows, files)
        _validate_klaviyo_events_page_publication(list(reversed(rows)), files, "events")

    def test_event_preflight_rejects_stream_mismatch_and_missing_required_column(self):
        rows, files, _, _ = publication_fixture()
        with self.assertRaisesRegex(ValueError, "Unknown Klaviyo stream"):
            _validate_klaviyo_events_page_publication(rows, files, "orders")
        rows, files, _, _ = publication_fixture()
        rows[0]["event_gid"] = None
        with self.assertRaisesRegex(ValueError, "required column"):
            _validate_klaviyo_events_page_publication(rows, files)

    def test_event_preflight_rejects_bad_variables_and_cursor(self):
        rows, files, _, _ = publication_fixture()
        files[0]["variables"]["sort"] = "datetime"
        with self.assertRaisesRegex(ValueError, "metadata"):
            _validate_klaviyo_events_page_publication(rows, files)
        rows, files, _, _ = publication_fixture()
        files[1]["variables"] = {"cursor": "https://evil.example/api/events?cursor=abc"}
        with self.assertRaisesRegex(ValueError, "cursor"):
            _validate_klaviyo_events_page_publication(rows, files)

    def test_event_preflight_rejects_foreign_metric_and_payload_identity_mismatch(self):
        rows, files, texts, _ = publication_fixture()
        rows[0]["metric_id"] = "M9"
        with self.assertRaisesRegex(ValueError, "another metric"):
            _validate_klaviyo_events_page_publication(rows, files)
        rows, files, texts, _ = publication_fixture()
        rows[0]["event_gid"] = "other"
        with self.assertRaisesRegex(ValueError, "event identity"):
            _validate_klaviyo_events_page_publication(rows, files)
        rows, files, _, _ = publication_fixture()
        rows[0]["original_payload"] = "{not json"
        with self.assertRaisesRegex(ValueError, "valid JSON"):
            _validate_klaviyo_events_page_publication(rows, files)

    def test_event_preflight_rejects_duplicates_and_sealed_count_gaps(self):
        rows, files, _, _ = publication_fixture()
        duplicated = [rows[0], dict(rows[0])]
        with self.assertRaisesRegex(ValueError, "unique per event identity"):
            _validate_klaviyo_events_page_publication(duplicated, files)
        rows, files, _, _ = publication_fixture()
        with self.assertRaisesRegex(ValueError, "sealed metric count"):
            _validate_klaviyo_events_page_publication(rows[:-1], files)
        rows, files, _, _ = publication_fixture()
        with self.assertRaisesRegex(ValueError, "completion seal"):
            _validate_klaviyo_events_page_publication(rows, files[:-1])
        empty_files = [dict(uri="gs://landing/complete.json", generation="9",
                            sha256="a" * 64, role="completion_seal", klaviyo_counts={"M1": 1})]
        with self.assertRaisesRegex(ValueError, "zero count"):
            _validate_klaviyo_events_page_publication([], empty_files)


if __name__ == "__main__":
    unittest.main()
