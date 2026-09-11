import json
import re
import unittest
from unittest.mock import patch

from agent.warehouse.klaviyo_campaigns_capture import KlaviyoCampaignsCapture
from agent.warehouse.refund_capture import CaptureError
from tests.test_catalog_capture import Blob, Bucket

BASE = "https://a.klaviyo.com/api/campaigns"
NEXT = BASE + "?page%5Bsize%5D=100&cursor=def"


def campaign(cid="C1", name="Summer Sale"):
    return {"type": "campaign", "id": cid,
            "attributes": {"definition": {"name": name, "builder": "wizard",
                                          "send_settings": {"send_strategy": "STATIC"}},
                           "created_at": "2026-09-01T00:00:00+00:00",
                           "updated_at": "2026-09-02T00:00:00+00:00"},
            "relationships": {"campaign-audiences": {"data": []},
                              "campaign-messages": {"data": []}}}


def audience(aid="A1", campaign_id="C1"):
    return {"type": "campaign-audience", "id": aid,
            "attributes": {"audience_name": "Audience 1", "priority": 0,
                           "has_non_draft_messages": False,
                           "included": ["LIST1"], "excluded": []},
            "relationships": {"campaign": {"data": {"type": "campaign", "id": campaign_id}}}}


def message(mid="M1", campaign_id="C1", audience_id="A1"):
    return {"type": "campaign-message", "id": mid,
            "attributes": {"name": "Summer email", "status": "SENT"},
            "relationships": {"campaign": {"data": {"type": "campaign", "id": campaign_id}},
                              "campaign-audience": {"data": {"type": "campaign-audience",
                                                             "id": audience_id}}}}


def variation(vid="V1", message_id="M1"):
    return {"type": "campaign-variation", "id": vid,
            "attributes": {"definition": {"name": "Email variation",
                                          "details": {"channel": "email", "subject": "Hi"}}},
            "relationships": {"campaign-message": {"data": {"type": "campaign-message",
                                                            "id": message_id}}}}


def page(campaigns, next_url=None, included=None):
    payload = {"data": campaigns, "links": {"next": next_url} if next_url else {}}
    if included is not None:
        payload["included"] = included
    return json.dumps(payload, separators=(",", ":")).encode()


def default_pages():
    return {
        None: page([campaign("C1")], next_url=NEXT,
                   included=[audience(), message(), variation()]),
        NEXT: page([campaign("C2")], included=[audience("A2", "C2")]),
    }


class Harness(KlaviyoCampaignsCapture):
    def __init__(self, *args, pages=None, **kwargs):
        self.responses = pages or default_pages()
        self.http_calls = []
        super().__init__(*args, **kwargs)

    def _http(self, url, params):
        key = None if params is not None else url
        self.http_calls.append((key, dict(params) if params is not None else None))
        body = self.responses.get(key)
        if body is None:
            raise CaptureError("unexpected request in simulated capture")
        return body


def make(bucket=None, pages=None, **overrides):
    kwargs = dict(bucket=bucket or Bucket(), token="token", account_key="klaviyo-main",
                  extraction_id="klaviyo-campaigns-test", pages=pages)
    kwargs.update(overrides)
    return Harness(**kwargs)


class KlaviyoCampaignsCaptureTests(unittest.TestCase):
    def test_cursor_pagination_and_seal(self):
        capture = make()
        seal = capture.collect()
        self.assertEqual(seal["counts"], {"campaigns": 2})
        self.assertEqual([p["operation"] for p in seal["pages"]], ["campaigns", "campaigns"])
        first = seal["pages"][0]["variables"]
        self.assertEqual(first["page[size]"], 100)
        self.assertEqual(first["sort"], "-updated_at")
        self.assertEqual(first["include"], "campaign-audiences,campaign-messages,campaign-variations")
        self.assertEqual(first["filter"], "equals(archived,false)")
        self.assertEqual(seal["pages"][1]["variables"], {"cursor": NEXT})
        self.assertEqual(seal["consistency"], "multi_request_observations_not_transactional_snapshot")

    def test_params_are_only_sent_on_the_first_request(self):
        capture = make()
        capture.collect()
        self.assertEqual([params is not None for _, params in capture.http_calls], [True, False])

    def test_unexpected_included_type_fails_closed(self):
        pages = default_pages()
        pages[NEXT] = page([campaign("C2")], included=[audience("A2", "C2"), {"type": "profile", "id": "P1"}])
        with self.assertRaisesRegex(CaptureError, "unexpected included resource"):
            make(pages=pages).collect()

    def test_audience_missing_parent_campaign_fails_closed(self):
        pages = default_pages()
        pages[NEXT] = page([campaign("C2")], included=[audience("A2", "C9")])
        with self.assertRaisesRegex(CaptureError, "campaign missing from the page"):
            make(pages=pages).collect()

    def test_message_missing_parent_audience_fails_closed(self):
        pages = default_pages()
        pages[NEXT] = page([campaign("C2")], included=[audience("A2", "C2"), message("M2", "C2", "A9")])
        with self.assertRaisesRegex(CaptureError, "audience missing from the page"):
            make(pages=pages).collect()

    def test_variation_missing_parent_message_fails_closed(self):
        pages = default_pages()
        pages[NEXT] = page([campaign("C2")], included=[audience("A2", "C2"), variation("V2", "M9")])
        with self.assertRaisesRegex(CaptureError, "message missing from the page"):
            make(pages=pages).collect()

    def test_empty_page_with_next_cursor_fails_closed(self):
        pages = default_pages()
        pages[NEXT] = page([], next_url=BASE + "?cursor=next2", included=[])
        with self.assertRaisesRegex(CaptureError, "Nonadvancing"):
            make(pages=pages).collect()

    def test_duplicate_campaign_across_pages_fails_closed(self):
        pages = default_pages()
        pages[NEXT] = page([campaign("C1")], included=[audience("A2", "C1")])
        with self.assertRaisesRegex(CaptureError, "Duplicate Klaviyo campaign"):
            make(pages=pages).collect()

    def test_page_limit_guard(self):
        with self.assertRaisesRegex(CaptureError, "page limit"):
            make(max_pages=1).collect()

    def test_bounds_and_identity_validation(self):
        with self.assertRaisesRegex(CaptureError, "account identity"):
            make(account_key="BAD KEY")
        with self.assertRaisesRegex(CaptureError, "credential"):
            make(token="")
        with self.assertRaises(CaptureError):
            make(max_pages=100001)
        with self.assertRaisesRegex(CaptureError, "bounds"):
            make(page_size=101)

    def test_read_only_replay_uses_no_http_and_fails_closed_on_tamper(self):
        capture = make()
        seal = capture.collect()
        replay = make(bucket=capture.bucket, read_only=True)
        self.assertEqual(replay.collect()["counts"], seal["counts"])
        self.assertEqual(replay.http_calls, [])
        with self.assertRaises(CaptureError):
            make(bucket=Bucket(), read_only=True)
        page_ref = seal["pages"][0]
        capture.bucket.objects[page_ref["uri"].split("fixture/", 1)[1]].metadata["response_sha256"] = "bad"
        with self.assertRaises(CaptureError):
            make(bucket=capture.bucket, read_only=True).collect()

    def test_conflicting_binding_fails_closed(self):
        capture = make()
        capture.collect()
        with self.assertRaisesRegex(CaptureError, "already bound"):
            make(bucket=capture.bucket, archived=True)


class FakeResponse:
    def __init__(self, status, headers=None, body=b""):
        self.status_code, self.headers, self.body = status, headers or {}, body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def iter_content(self, chunk_size=None):
        return iter([self.body])


class FakeSession:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, params=None, headers=None, stream=False, allow_redirects=False, timeout=None):
        self.calls.append((url, params, headers))
        return self.responses.pop(0)


class TransportTests(unittest.TestCase):
    def make_capture(self):
        return KlaviyoCampaignsCapture(bucket=Bucket(), token="token", account_key="klaviyo-main",
                                       extraction_id="transport-test")

    def test_429_reads_retry_after_without_consuming_backoff(self):
        body = page([], included=[])
        session = FakeSession([
            FakeResponse(429, {"Retry-After": "7"}),
            FakeResponse(429, {"Retry-After": "2"}),
            FakeResponse(200, body=body),
        ])
        sleeps = []
        with patch("agent.warehouse.klaviyo_campaigns_capture.requests.Session", return_value=session), \
                patch("agent.warehouse.klaviyo_campaigns_capture.time.sleep", side_effect=sleeps.append):
            capture = self.make_capture()
            seal = capture.collect()
        self.assertEqual(seal["counts"], {"campaigns": 0})
        self.assertEqual(sleeps, [7, 2])
        self.assertEqual(len(session.calls), 3)
        _, _, headers = session.calls[0]
        self.assertEqual(headers["revision"], "2026-07-15.pre")
        self.assertEqual(headers["accept"], "application/vnd.api+json")
        self.assertTrue(headers["Authorization"].startswith("Klaviyo-API-Key "))

    def test_429_without_retry_after_fails_closed(self):
        body = page([], included=[])
        session = FakeSession([FakeResponse(429), FakeResponse(200, body=body)])
        with patch("agent.warehouse.klaviyo_campaigns_capture.requests.Session", return_value=session), \
                patch("agent.warehouse.klaviyo_campaigns_capture.time.sleep"):
            with self.assertRaisesRegex(CaptureError, "Retry-After"):
                self.make_capture().collect()

    def test_5xx_backoff_then_success(self):
        body = page([campaign("C1")], included=[audience()])
        session = FakeSession([FakeResponse(503), FakeResponse(200, body=body)])
        sleeps = []
        with patch("agent.warehouse.klaviyo_campaigns_capture.requests.Session", return_value=session), \
                patch("agent.warehouse.klaviyo_campaigns_capture.time.sleep", side_effect=sleeps.append):
            seal = self.make_capture().collect()
        self.assertEqual(seal["counts"], {"campaigns": 1})
        self.assertEqual(sleeps, [10])

    def test_other_client_status_fails_closed(self):
        session = FakeSession([FakeResponse(403)])
        with patch("agent.warehouse.klaviyo_campaigns_capture.requests.Session", return_value=session):
            with self.assertRaisesRegex(CaptureError, "403"):
                self.make_capture().collect()

    def test_cursor_origin_is_pinned(self):
        pages = default_pages()
        pages[NEXT] = page([campaign("C2")], next_url="https://evil.example/api/campaigns?cursor=x",
                           included=[audience("A2", "C2")])
        with self.assertRaisesRegex(CaptureError, "origin"):
            make(pages=pages).collect()


if __name__ == "__main__":
    unittest.main()
