import unittest

from agent.warehouse.klaviyo_queries import (API_REVISION, EVENTS_BASE_URL, KlaviyoMetric,
                                             KlaviyoRequestError, compile_klaviyo_event_plan,
                                             compile_klaviyo_event_plans, validate_klaviyo_window)

START = "2026-09-09T12:00:00Z"
END = "2026-09-09T13:00:00Z"


class KlaviyoQueryTests(unittest.TestCase):
    def test_first_request_params_match_the_events_contract(self):
        plan = compile_klaviyo_event_plan("M1", None, START, END)
        self.assertEqual(plan.metric_id, "M1")
        self.assertEqual(plan.first_params["page[size]"], 200)
        self.assertEqual(plan.first_params["sort"], "-datetime")
        self.assertEqual(plan.first_params["include"], "profile")
        self.assertEqual(
            plan.first_params["filter"],
            'greater-or-equal(datetime,2026-09-09T12:00:00+00:00),'
            'less-than(datetime,2026-09-09T13:00:00+00:00),equals(metric_id,"M1")')

    def test_cursor_requests_keep_params_off_the_chain(self):
        plan = compile_klaviyo_event_plan("M1", None, START, END)
        cursor = EVENTS_BASE_URL + "?page[size]=200&cursor=abc"
        self.assertIsNone(plan.request_params(cursor_url=cursor))
        with self.assertRaises(KlaviyoRequestError):
            plan.request_params(cursor_url="https://evil.example/api/events?cursor=abc")

    def test_rejects_bad_metric_window_and_page_size(self):
        for metric_id, start, end, size in (
                ("", START, END, 200), (None, START, END, 200), ('M",x', START, END, 200)):
            with self.assertRaises(KlaviyoRequestError):
                compile_klaviyo_event_plan(metric_id, None, start, end, size)
        with self.assertRaises(KlaviyoRequestError):
            compile_klaviyo_event_plan("M1", None, "2026-09-09T12:00:00", END)
        with self.assertRaises(KlaviyoRequestError):
            compile_klaviyo_event_plan("M1", None, END, START)
        with self.assertRaises(KlaviyoRequestError):
            compile_klaviyo_event_plan("M1", None, START, START)
        with self.assertRaises(KlaviyoRequestError):
            compile_klaviyo_event_plan("M1", None, START, END, 201)
        with self.assertRaises(KlaviyoRequestError):
            compile_klaviyo_event_plan("M1", None, START, END, True)

    def test_priority_order_is_preserved_and_duplicates_rejected(self):
        plans = compile_klaviyo_event_plans(
            [{"metric_id": "send"}, {"metric_id": "open", "event_type": "emailOpen"}],
            START, END)
        self.assertEqual([plan.metric_id for plan in plans], ["send", "open"])
        self.assertEqual(plans[1].event_type, "emailOpen")
        self.assertEqual(plans[0].event_type, None)
        with self.assertRaises(KlaviyoRequestError):
            compile_klaviyo_event_plans([{"metric_id": "a"}, {"metric_id": "a"}], START, END)
        with self.assertRaises(KlaviyoRequestError):
            compile_klaviyo_event_plans([], START, END)
        with self.assertRaises(KlaviyoRequestError):
            compile_klaviyo_event_plans(["a"], START, END)

    def test_revision_and_base_url_contract(self):
        self.assertEqual(API_REVISION, "2025-07-15")
        self.assertEqual(EVENTS_BASE_URL, "https://a.klaviyo.com/api/events")

    def test_window_validation_is_shared(self):
        start, end = validate_klaviyo_window(START, "2026-09-09T16:00:00+02:00")
        self.assertEqual(end.isoformat(), "2026-09-09T14:00:00+00:00")
        self.assertTrue(start < end)
        with self.assertRaises(KlaviyoRequestError):
            validate_klaviyo_window(END, START)


if __name__ == "__main__":
    unittest.main()
