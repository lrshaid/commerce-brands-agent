import unittest

from agent.semantic.serving import ServingContract


class ServingContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = ServingContract()

    def test_contract_validates(self):
        self.assertEqual(self.contract.validate(), [])

    def test_every_served_metric_is_in_catalog(self):
        for name in self.contract.metrics:
            self.assertIn(name, self.contract.model.metrics, name)

    def test_blocked_metrics_excluded_from_servable(self):
        servable = set(self.contract.servable())
        # Still blocked: EMV (exchangeV2s invalid) and traffic (no GA4 export).
        for blocked in ("emv", "traffic"):
            self.assertNotIn(blocked, servable, blocked)
        # Servable on the real reconciled marts (run 55ac2056, 2026-09-13),
        # including RMV/NMV now that refunds are published.
        for ok in ("gmv", "nmv", "rmv", "order_count", "gross_units", "net_units"):
            self.assertIn(ok, servable, ok)

    def test_resolve_base_metric_maps_to_real_mart_column(self):
        plan = self.contract.resolve("nmv")
        self.assertEqual(plan["mart"], "metric_revenue_daily")
        self.assertEqual(plan["value_column"], "nmv_amount")
        self.assertEqual(plan["aggregation"], "sum")
        self.assertEqual(plan["time_column"], "metric_date")
        self.assertEqual(plan["tenant_column"], "shop_key")
        self.assertIn("NMV assumes EMV=0 (exchange contract exchangeV2s invalid)", plan["honesty_flags"])

    def test_resolve_derived_metric_expands_to_ratio(self):
        plan = self.contract.resolve("aov")
        self.assertEqual(plan["kind"], "derived")
        self.assertEqual(plan["numerator"]["value_column"], "gmv_amount")
        self.assertEqual(plan["denominator"]["value_column"], "orders")


if __name__ == "__main__":
    unittest.main()
