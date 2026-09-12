import unittest

from agent.warehouse.klaviyo_campaigns_queries import (API_REVISION, CAMPAIGNS_BASE_URL, MESSAGES_BASE_URL,
                                                       CAMPAIGNS_OPERATION, MESSAGES_OPERATION,
                                                       KlaviyoCampaignsRequestError,
                                                       compile_klaviyo_campaigns_plans)


class KlaviyoCampaignsQueriesTests(unittest.TestCase):
    def test_two_chain_snapshot_plan(self):
        campaigns_plan, messages_plan = compile_klaviyo_campaigns_plans()
        self.assertEqual(campaigns_plan.operation, CAMPAIGNS_OPERATION)
        self.assertEqual(campaigns_plan.first_params, {
            "page[size]": 100, "sort": "-updated_at",
            "include": "campaign-audiences,campaign-messages"})
        self.assertIsNone(campaigns_plan.archived)
        self.assertEqual(messages_plan.operation, MESSAGES_OPERATION)
        self.assertEqual(messages_plan.first_params, {
            "page[size]": 100, "sort": "-updated",
            "include": "campaign,campaign-variations"})
        self.assertEqual(campaigns_plan.request_params(), campaigns_plan.first_params)
        self.assertEqual(messages_plan.request_params(), messages_plan.first_params)

    def test_archived_filter_is_explicit_when_requested(self):
        self.assertEqual(compile_klaviyo_campaigns_plans(archived=False)[0].first_params["filter"],
                         "equals(archived,false)")
        self.assertEqual(compile_klaviyo_campaigns_plans(archived=True)[0].first_params["filter"],
                         "equals(archived,true)")
        self.assertNotIn("filter", compile_klaviyo_campaigns_plans()[0].first_params)

    def test_cursors_must_stay_on_their_endpoint_origin(self):
        campaigns_plan, messages_plan = compile_klaviyo_campaigns_plans()
        self.assertIsNone(campaigns_plan.request_params(
            cursor_url=CAMPAIGNS_BASE_URL + "?page[size]=100&cursor=abc"))
        self.assertIsNone(messages_plan.request_params(
            cursor_url=MESSAGES_BASE_URL + "?page[size]=100&cursor=abc"))
        with self.assertRaisesRegex(KlaviyoCampaignsRequestError, "origin"):
            campaigns_plan.request_params(cursor_url="https://evil.example/api/campaigns?cursor=abc")
        with self.assertRaisesRegex(KlaviyoCampaignsRequestError, "origin"):
            messages_plan.request_params(cursor_url=CAMPAIGNS_BASE_URL + "?cursor=abc")

    def test_archived_filter_accepts_only_booleans_or_none(self):
        for value in ("false", 0):
            with self.assertRaisesRegex(KlaviyoCampaignsRequestError, "boolean"):
                compile_klaviyo_campaigns_plans(archived=value)

    def test_page_size_bounds_follow_the_api_maximum(self):
        self.assertEqual(compile_klaviyo_campaigns_plans(page_size=1)[0].first_params["page[size]"], 1)
        self.assertEqual(compile_klaviyo_campaigns_plans(page_size=100)[0].first_params["page[size]"], 100)
        for value in (0, 101, "50", True):
            with self.assertRaisesRegex(KlaviyoCampaignsRequestError, "page size"):
                compile_klaviyo_campaigns_plans(page_size=value)

    def test_revision_and_base_url_contract(self):
        self.assertEqual(API_REVISION, "2026-07-15.pre")
        self.assertEqual(CAMPAIGNS_BASE_URL, "https://a.klaviyo.com/api/campaigns")
        self.assertEqual(MESSAGES_BASE_URL, "https://a.klaviyo.com/api/campaign-messages")


if __name__ == "__main__":
    unittest.main()
