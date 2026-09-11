import unittest

from agent.warehouse.klaviyo_campaigns_queries import (API_REVISION, CAMPAIGNS_BASE_URL,
                                                       KlaviyoCampaignsRequestError,
                                                       compile_klaviyo_campaigns_plan)


class KlaviyoCampaignsQueriesTests(unittest.TestCase):
    def test_first_request_params_match_the_snapshot_contract(self):
        plan = compile_klaviyo_campaigns_plan()
        self.assertEqual(plan.first_params, {
            "page[size]": 100, "sort": "-updated_at",
            "include": "campaign-audiences,campaign-messages,campaign-variations",
            "filter": "equals(archived,false)"})
        self.assertEqual(plan.request_params(), plan.first_params)

    def test_cursor_must_stay_on_the_campaigns_origin(self):
        plan = compile_klaviyo_campaigns_plan()
        self.assertIsNone(plan.request_params(cursor_url=CAMPAIGNS_BASE_URL + "?page[size]=100&cursor=abc"))
        with self.assertRaisesRegex(KlaviyoCampaignsRequestError, "origin"):
            plan.request_params(cursor_url="https://evil.example/api/campaigns?cursor=abc")

    def test_archived_filter_accepts_only_booleans(self):
        self.assertEqual(compile_klaviyo_campaigns_plan(archived=True).first_params["filter"],
                         "equals(archived,true)")
        for value in ("false", 0, None):
            with self.assertRaisesRegex(KlaviyoCampaignsRequestError, "boolean"):
                compile_klaviyo_campaigns_plan(archived=value)

    def test_page_size_bounds_follow_the_api_maximum(self):
        self.assertEqual(compile_klaviyo_campaigns_plan(page_size=1).first_params["page[size]"], 1)
        self.assertEqual(compile_klaviyo_campaigns_plan(page_size=100).first_params["page[size]"], 100)
        for value in (0, 101, "50", True):
            with self.assertRaisesRegex(KlaviyoCampaignsRequestError, "page size"):
                compile_klaviyo_campaigns_plan(page_size=value)

    def test_revision_and_base_url_contract(self):
        self.assertEqual(API_REVISION, "2026-07-15.pre")
        self.assertEqual(CAMPAIGNS_BASE_URL, "https://a.klaviyo.com/api/campaigns")


if __name__ == "__main__":
    unittest.main()
