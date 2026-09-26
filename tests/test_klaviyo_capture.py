import json
import re
import unittest
from unittest.mock import patch

from agent.warehouse.klaviyo_capture import KlaviyoCapture
from agent.warehouse.refund_capture import CaptureError
from tests.test_catalog_capture import Blob, Bucket

BASE = "https://a.klaviyo.com/api/events"
NEXT = {"M2": BASE + "?page%5Bsize%5D=200&filter=ok&cursor=m2",
        "M1": BASE + "?page%5Bsize%5D=200&filter=ok&cursor=def"}

METRICS = [{"metric_id": "M2", "event_type": "emailReceived"},
           {"metric_id": "M1", "event_type": "emailOpen"}]


def profile(pid="P1", email="person@example.com"):
    return {"type": "profile", "id": pid, "attributes": {"email": email}}


def event(event_id, metric_id="M1", profile_id="P1", properties=None):
    relationships = {"metric": {"data": {"type": "metric", "id": metric_id}}}
    if profile_id:
        relationships["profile"] = {"data": {"type": "profile", "id": profile_id}}
    return {"type": "event", "id": event_id,
            "attributes": {"datetime": "2026-09-09T12:10:00+00:00", "timestamp": 1757419800,
                           "uuid": f"uuid-{event_id}", "event_properties": properties or {}},
            "relationships": relationships}


def page(events, next_url=None, included=None):
    payload = {"data": events, "links": {"next": next_url} if next_url else {}}
    if included is not None:
        payload["included"] = included
    return json.dumps(payload, separators=(",", ":")).encode()


def default_pages():
    return {
        ("M2", None): page([event("e2", "M2")], next_url=NEXT["M2"], included=[profile("P1")]),
        ("M1", None): page([event("e1", "M1")], next_url=NEXT["M1"], included=[profile("P1")]),
        ("M2", NEXT["M2"]): page([], included=[profile("P1")]),
        ("M1", NEXT["M1"]): page([event("e3", "M1")], included=[profile("P1")]),
    }


class Harness(KlaviyoCapture):
    def __init__(self, *args, pages=None, **kwargs):
        self.responses = pages or default_pages()
        self.http_calls = []
        super().__init__(*args, **kwargs)

    def _http(self, url, params):
        if params is not None:
            metric = re.search(r'equals\(metric_id,"([^"]+)"\)', params["filter"]).group(1)
            key = None
        else:
            owners = [metric for metric, cursor in self.responses if cursor == url]
            if len(owners) != 1:
                raise CaptureError("unexpected request in simulated capture")
            metric, key = owners[0], url
        self.http_calls.append((key, dict(params) if params is not None else None))
        body = self.responses.get((metric, key))
        if body is None:
            raise CaptureError("unexpected request in simulated capture")
        return body


def make(bucket=None, pages=None, **overrides):
    kwargs = dict(bucket=bucket or Bucket(), token="token", account_key="klaviyo-main",
                  extraction_id="klaviyo-test", metrics=METRICS,
                  window_start="2026-09-09T12:00:00Z", window_end="2026-09-09T13:00:00Z",
                  pages=pages)
    kwargs.update(overrides)
    return Harness(**kwargs)


class KlaviyoCaptureTests(unittest.TestCase):
    def test_priority_order_cursor_pagination_and_seal(self):
        capture = make()
        seal = capture.collect()
        self.assertEqual(seal["counts"], {"M2": 1, "M1": 2})
        self.assertEqual([p["operation"] for p in seal["pages"]], ["M2", "M2", "M1", "M1"])
        first = seal["pages"][0]["variables"]
        self.assertEqual(first["page[size]"], 200)
        self.assertEqual(first["sort"], "-datetime")
        self.assertEqual(first["include"], "profile")
        self.assertIn('equals(metric_id,"M2")', first["filter"])
        self.assertEqual(seal["pages"][1]["variables"], {"cursor": NEXT["M2"]})
        self.assertEqual(seal["consistency"], "multi_request_observations_not_transactional_snapshot")

    def test_params_are_only_sent_on_the_first_request(self):
        capture = make()
        capture.collect()
        self.assertEqual([params is not None for _, params in capture.http_calls],
                         [True, False, True, False])
        self.assertEqual(capture.http_calls[0][1]["filter"], capture.plans[0].first_params["filter"])

    def test_foreign_metric_page_fails_closed(self):
        pages = default_pages()
        pages[("M1", NEXT["M1"])] = page([event("e3", "M2")], included=[profile("P1")])
        with self.assertRaisesRegex(CaptureError, "filtered metric"):
            make(pages=pages).collect()

    def test_missing_included_profile_keeps_the_event_without_email(self):
        # Profiles deleted from Klaviyo (GDPR) never resolve in included[];
        # historical events referencing them stay real, with profile_gid and
        # a NULL email projection.
        pages = default_pages()
        pages[("M1", NEXT["M1"])] = page([event("e3", "M1")], included=[])
        seal = make(pages=pages).collect()
        self.assertEqual(seal["counts"], {"M2": 1, "M1": 2})

    def test_unexpected_included_resource_fails_closed(self):
        pages = default_pages()
        pages[("M1", None)] = page([event("e1")], next_url=NEXT["M1"],
                                   included=[profile("P1"), {"type": "metric", "id": "M1"}])
        with self.assertRaisesRegex(CaptureError, "included resource"):
            make(pages=pages).collect()

    def test_empty_page_with_next_cursor_fails_closed(self):
        pages = default_pages()
        pages[("M2", NEXT["M2"])] = page([], next_url=BASE + "?cursor=next2", included=[profile("P1")])
        with self.assertRaisesRegex(CaptureError, "Nonadvancing"):
            make(pages=pages).collect()

    def test_duplicate_event_across_pages_fails_closed(self):
        pages = default_pages()
        pages[("M1", NEXT["M1"])] = page([event("e1")], included=[profile("P1")])
        with self.assertRaisesRegex(CaptureError, "Duplicate"):
            make(pages=pages).collect()

    def test_page_limit_guard_is_per_stream(self):
        with self.assertRaisesRegex(CaptureError, "page limit"):
            make(max_pages=1).collect()

    def test_bounds_and_identity_validation(self):
        with self.assertRaisesRegex(CaptureError, "account identity"):
            make(account_key="BAD KEY")
        with self.assertRaisesRegex(CaptureError, "credential"):
            make(token="")
        with self.assertRaises(CaptureError):
            make(max_pages=100001)

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
            make(bucket=capture.bucket, metrics=[{"metric_id": "M9"}])


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
        return KlaviyoCapture(bucket=Bucket(), token="token", account_key="klaviyo-main",
                              extraction_id="transport-test",
                              metrics=[{"metric_id": "M1"}],
                              window_start="2026-09-09T12:00:00Z",
                              window_end="2026-09-09T13:00:00Z")

    def test_429_reads_retry_after_without_consuming_backoff(self):
        body = page([], included=[])
        session = FakeSession([
            FakeResponse(429, {"Retry-After": "7"}),
            FakeResponse(429, {"Retry-After": "2"}),
            FakeResponse(200, body=body),
        ])
        sleeps = []
        with patch("agent.warehouse.klaviyo_capture.requests.Session", return_value=session), \
                patch("agent.warehouse.klaviyo_capture.time.sleep", side_effect=sleeps.append):
            capture = self.make_capture()
            seal = capture.collect()
        self.assertEqual(seal["counts"], {"M1": 0})
        self.assertEqual(sleeps, [7, 2])
        self.assertEqual(len(session.calls), 3)
        _, _, headers = session.calls[0]
        self.assertEqual(headers["revision"], "2025-07-15")
        self.assertEqual(headers["accept"], "application/vnd.api+json")
        self.assertTrue(headers["Authorization"].startswith("Klaviyo-API-Key "))

    def test_429_without_retry_after_fails_closed(self):
        body = page([], included=[])
        session = FakeSession([FakeResponse(429), FakeResponse(200, body=body)])
        with patch("agent.warehouse.klaviyo_capture.requests.Session", return_value=session), \
                patch("agent.warehouse.klaviyo_capture.time.sleep"):
            with self.assertRaisesRegex(CaptureError, "Retry-After"):
                self.make_capture().collect()

    def test_5xx_backoff_then_success(self):
        body = page([event("e1")], included=[profile("P1")])
        session = FakeSession([FakeResponse(503), FakeResponse(200, body=body)])
        sleeps = []
        with patch("agent.warehouse.klaviyo_capture.requests.Session", return_value=session), \
                patch("agent.warehouse.klaviyo_capture.time.sleep", side_effect=sleeps.append):
            seal = self.make_capture().collect()
        self.assertEqual(seal["counts"], {"M1": 1})
        self.assertEqual(sleeps, [10])

    def test_repeated_5xx_exhausts_bounded_backoff(self):
        session = FakeSession([FakeResponse(500)] * 6)
        with patch("agent.warehouse.klaviyo_capture.requests.Session", return_value=session), \
                patch("agent.warehouse.klaviyo_capture.time.sleep"):
            with self.assertRaisesRegex(CaptureError, "backoff"):
                self.make_capture().collect()

    def test_other_client_status_fails_closed(self):
        session = FakeSession([FakeResponse(403)])
        with patch("agent.warehouse.klaviyo_capture.requests.Session", return_value=session):
            with self.assertRaisesRegex(CaptureError, "403"):
                self.make_capture().collect()

    def test_cursor_origin_is_pinned(self):
        pages = default_pages()
        pages[("M2", NEXT["M2"])] = page([event("e4", "M2")],
                                   next_url="https://evil.example/api/events?cursor=x",
                                   included=[profile("P1")])
        pages[("M1", NEXT["M1"])] = page([], included=[profile("P1")])
        capture = make(pages=pages)
        with self.assertRaisesRegex(CaptureError, "origin"):
            capture.collect()


if __name__ == "__main__":
    unittest.main()
