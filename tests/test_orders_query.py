import unittest
from datetime import datetime, timedelta, timezone

from queries.shopify.orders_query import build_orders_bulk_query


class OrdersQueryTest(unittest.TestCase):
    def test_complete_refresh_has_no_query_filter(self):
        query = build_orders_bulk_query()
        self.assertIn("orders {", query)
        self.assertNotIn("updated_at:", query)

    def test_incremental_window_is_utc_and_half_open(self):
        local = timezone(timedelta(hours=-3))
        query = build_orders_bulk_query(
            datetime(2026, 8, 27, 9, tzinfo=local),
            datetime(2026, 8, 28, 9, tzinfo=local),
        )
        self.assertIn("updated_at:>='2026-08-27T12:00:00Z'", query)
        self.assertIn("updated_at:<'2026-08-28T12:00:00Z'", query)
        self.assertIn(" AND ", query)

    def test_open_ended_start(self):
        query = build_orders_bulk_query(
            start=datetime(2026, 8, 27, tzinfo=timezone.utc)
        )
        self.assertIn("updated_at:>='2026-08-27T00:00:00Z'", query)
        self.assertNotIn("updated_at:<", query)

    def test_naive_timestamp_is_rejected(self):
        with self.assertRaises(ValueError):
            build_orders_bulk_query(start=datetime(2026, 8, 27))

    def test_recovered_selector_contains_major_subobjects(self):
        query = build_orders_bulk_query()
        for field in (
            "customerJourneySummary",
            "fulfillments",
            "refunds",
            "lineItems",
            "shippingLines",
            "discountApplications",
        ):
            self.assertIn(field, query)


if __name__ == "__main__":
    unittest.main()
