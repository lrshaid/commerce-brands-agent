import unittest
from datetime import datetime, timezone
import hashlib
import json

from agent.warehouse.klaviyo_campaigns_raw import prepare_klaviyo_campaigns_raw
from agent.warehouse.raw_publication import (_validate_klaviyo_campaigns_page_publication,
                                             contract_columns)
from tests.test_catalog_capture import Blob, Bucket
from tests.test_klaviyo_campaigns_capture import (CAMPAIGNS, CAMPAIGNS_NEXT, MESSAGES, MESSAGES_NEXT,
                                                  Harness, audience, campaign, message, page, variation)

NOW = datetime.now(timezone.utc)


def capture_responses():
    return {
        (CAMPAIGNS, None): page([campaign("C1")], next_url=CAMPAIGNS_NEXT,
                                included=[audience(), message()]),
        (CAMPAIGNS, CAMPAIGNS_NEXT): page([campaign("C2")], included=[audience("A2", "C2")]),
        (MESSAGES, None): page([message("M1")], next_url=MESSAGES_NEXT,
                               included=[campaign("C1"), variation()]),
        (MESSAGES, MESSAGES_NEXT): page([message("M2", "C1")],
                                        included=[campaign("C1"), variation("V2", "M2")]),
    }


class UniqueGenerationBucket(Bucket):
    def blob(self, name):
        if name not in self.objects:
            self.objects[name] = Blob(name, generation=len(self.objects) + 1)
        return self.objects[name]


def capture_and_prepare(pages=None):
    capture = Harness(bucket=UniqueGenerationBucket(), token="token",
                      account_key="klaviyo-main", extraction_id="klaviyo-campaigns-raw",
                      pages=pages or capture_responses())
    seal = capture.collect()
    prepared = prepare_klaviyo_campaigns_raw(
        bucket=capture.bucket, token="token", account_key="klaviyo-main",
        extraction_id="klaviyo-campaigns-raw", ingested_at=NOW)
    return capture, seal, prepared


class KlaviyoCampaignsRawTests(unittest.TestCase):
    def test_one_row_per_page_with_seal_last_and_valid_grain(self):
        _, seal, prepared = capture_and_prepare()
        campaigns = prepared["streams"]["campaigns"]
        self.assertEqual(set(prepared["streams"]), {"campaigns"})
        self.assertEqual(campaigns["raw_record_count"], 4)
        self.assertEqual(prepared["raw_record_count"], 4)
        self.assertEqual(campaigns["counts"], seal["counts"])
        self.assertEqual([f["role"] for f in campaigns["files"]],
                         ["response_page"] * 4 + ["completion_seal"])
        _validate_klaviyo_campaigns_page_publication(list(campaigns["records"]), campaigns["files"], "campaigns")

    def test_rows_preserve_exact_body_and_envelope(self):
        _, _, prepared = capture_and_prepare()
        row = next(prepared["streams"]["campaigns"]["records"])
        raw, _ = contract_columns()
        self.assertEqual(set(row), set(raw))
        self.assertEqual(row["shop_key"], "klaviyo-main")
        self.assertEqual(row["extraction_id"], "klaviyo-campaigns-raw")
        self.assertEqual(row["record_index"], 1)
        self.assertEqual(row["api_version"], "2026-07-15.pre")
        self.assertEqual(row["payload"], row["record_text"])
        self.assertEqual(row["record_sha256"], hashlib.sha256(row["record_text"].encode()).hexdigest())

    def test_empty_snapshot_keeps_seal_only_rows(self):
        empty = page([], included=[])
        _, _, prepared = capture_and_prepare(pages={
            (CAMPAIGNS, None): empty,
            (MESSAGES, None): empty,
        })
        campaigns = prepared["streams"]["campaigns"]
        self.assertEqual(campaigns["raw_record_count"], 2)
        self.assertEqual(campaigns["counts"], {"campaigns_list": 0, "messages_list": 0})
        _validate_klaviyo_campaigns_page_publication(list(campaigns["records"]), campaigns["files"], "campaigns")


def publication_fixture():
    raw, _ = contract_columns()
    seal = {"binding": {}, "status": "captured",
            "counts": {"campaigns_list": 2, "messages_list": 2}}
    texts = {
        101: json.dumps({"data": [campaign("C1")],
                         "included": [audience(), message()]}, separators=(",", ":")),
        102: json.dumps({"data": [campaign("C2")],
                         "included": [audience("A2", "C2")]}, separators=(",", ":")),
        103: json.dumps({"data": [message("M1")],
                         "included": [campaign("C1"), variation()]}, separators=(",", ":")),
        104: json.dumps({"data": [message("M2", "C1")],
                         "included": [campaign("C1"), variation("V2", "M2")]}, separators=(",", ":")),
        105: json.dumps(seal, separators=(",", ":")),
    }
    files = [
        dict(uri="gs://landing/pages/101.json", generation="101",
             sha256=hashlib.sha256(texts[101].encode()).hexdigest(),
             request_sha256="b" * 64, operation="campaigns_list",
             variables={"page[size]": 100, "sort": "-updated_at",
                        "include": "campaign-audiences,campaign-messages"},
             captured_at="2026-09-11T12:30:00+00:00", role="response_page"),
        dict(uri="gs://landing/pages/102.json", generation="102",
             sha256=hashlib.sha256(texts[102].encode()).hexdigest(),
             request_sha256="c" * 64, operation="campaigns_list",
             variables={"cursor": CAMPAIGNS + "?page%5Bsize%5D=100&cursor=abc"},
             captured_at="2026-09-11T12:30:05+00:00", role="response_page"),
        dict(uri="gs://landing/pages/103.json", generation="103",
             sha256=hashlib.sha256(texts[103].encode()).hexdigest(),
             request_sha256="d" * 64, operation="messages_list",
             variables={"page[size]": 100, "sort": "-updated",
                        "include": "campaign,campaign-variations"},
             captured_at="2026-09-11T12:30:10+00:00", role="response_page"),
        dict(uri="gs://landing/pages/104.json", generation="104",
             sha256=hashlib.sha256(texts[104].encode()).hexdigest(),
             request_sha256="e" * 64, operation="messages_list",
             variables={"cursor": MESSAGES + "?page%5Bsize%5D=100&cursor=def"},
             captured_at="2026-09-11T12:30:15+00:00", role="response_page"),
        dict(uri="gs://landing/pages/complete.json", generation="105",
             sha256=hashlib.sha256(texts[105].encode()).hexdigest(), role="completion_seal"),
    ]
    rows = []
    for generation, text in texts.items():
        if generation == 105:
            continue
        row = dict.fromkeys(raw)
        row.update(shop_key="klaviyo-main", extraction_id="klaviyo-1", file_id=str(generation),
                   record_index=1, query_sha256="f" * 64, request_sha256="0" * 64,
                   api_version="2026-07-15.pre", ingested_at="2026-09-11T13:00:00+00:00",
                   record_sha256=hashlib.sha256(text.encode()).hexdigest(),
                   record_text=text, payload=text, object_gid=None, parent_gid=None)
        rows.append(row)
    return rows, files, texts


class KlaviyoCampaignsPublicationTests(unittest.TestCase):
    def test_page_preflight_accepts_both_chains_params_and_cursor_pages(self):
        rows, files, _ = publication_fixture()
        _validate_klaviyo_campaigns_page_publication(rows, files)
        _validate_klaviyo_campaigns_page_publication(list(reversed(rows)), files, "campaigns")

    def test_page_preflight_rejects_checksum_and_stream_mismatch(self):
        rows, files, _ = publication_fixture()
        rows[0]["record_sha256"] = "1" * 64
        with self.assertRaisesRegex(ValueError, "checksum"):
            _validate_klaviyo_campaigns_page_publication(rows, files)
        rows, files, _ = publication_fixture()
        with self.assertRaisesRegex(ValueError, "Unknown Klaviyo stream"):
            _validate_klaviyo_campaigns_page_publication(rows, files, "events")

    def test_page_preflight_rejects_bad_variables_and_cursor(self):
        rows, files, _ = publication_fixture()
        files[0]["variables"]["sort"] = "updated_at"
        with self.assertRaisesRegex(ValueError, "metadata"):
            _validate_klaviyo_campaigns_page_publication(rows, files)
        rows, files, _ = publication_fixture()
        files[2]["variables"]["include"] = "campaign,campaign-audience"
        with self.assertRaisesRegex(ValueError, "metadata"):
            _validate_klaviyo_campaigns_page_publication(rows, files)
        rows, files, _ = publication_fixture()
        files[1]["variables"] = {"cursor": "https://evil.example/api/campaigns?cursor=abc"}
        with self.assertRaisesRegex(ValueError, "cursor"):
            _validate_klaviyo_campaigns_page_publication(rows, files)
        rows, files, _ = publication_fixture()
        files[3]["variables"] = {"cursor": "https://a.klaviyo.com/api/campaigns?cursor=x"}
        with self.assertRaisesRegex(ValueError, "cursor"):
            _validate_klaviyo_campaigns_page_publication(rows, files)
        rows, files, _ = publication_fixture()
        files[0]["operation"] = "events"
        with self.assertRaisesRegex(ValueError, "operation"):
            _validate_klaviyo_campaigns_page_publication(rows, files)

    def test_page_preflight_rejects_broken_hierarchy(self):
        rows, files, texts = publication_fixture()
        body = json.loads(texts[101])
        body["included"][0]["relationships"]["campaign"]["data"]["id"] = "C9"
        text = json.dumps(body, separators=(",", ":"))
        rows[0]["record_text"] = rows[0]["payload"] = text
        rows[0]["record_sha256"] = files[0]["sha256"] = hashlib.sha256(text.encode()).hexdigest()
        with self.assertRaisesRegex(ValueError, "parent missing from the page"):
            _validate_klaviyo_campaigns_page_publication(rows, files)
        rows, files, texts = publication_fixture()
        body = json.loads(texts[103])
        body["included"][1]["relationships"]["campaign-message"]["data"]["id"] = "M9"
        text = json.dumps(body, separators=(",", ":"))
        rows[2]["record_text"] = rows[2]["payload"] = text
        rows[2]["record_sha256"] = files[2]["sha256"] = hashlib.sha256(text.encode()).hexdigest()
        with self.assertRaisesRegex(ValueError, "message missing from the page"):
            _validate_klaviyo_campaigns_page_publication(rows, files)
        rows, files, texts = publication_fixture()
        text = json.dumps({"data": [campaign("C1")],
                           "included": [{"type": "profile", "id": "P1"}]}, separators=(",", ":"))
        rows[0]["record_text"] = rows[0]["payload"] = text
        rows[0]["record_sha256"] = files[0]["sha256"] = hashlib.sha256(text.encode()).hexdigest()
        with self.assertRaisesRegex(ValueError, "unexpected included resource"):
            _validate_klaviyo_campaigns_page_publication(rows, files)

    def test_page_preflight_rejects_missing_seal_or_bad_empty_count(self):
        rows, files, _ = publication_fixture()
        with self.assertRaisesRegex(ValueError, "completion seal"):
            _validate_klaviyo_campaigns_page_publication(rows, files[:-1])
        empty_files = [dict(uri="gs://landing/complete.json", generation="9",
                            sha256="a" * 64, role="completion_seal",
                            klaviyo_counts={"campaigns_list": 0, "messages_list": 1})]
        with self.assertRaisesRegex(ValueError, "zero count"):
            _validate_klaviyo_campaigns_page_publication([], empty_files)


if __name__ == "__main__":
    unittest.main()
