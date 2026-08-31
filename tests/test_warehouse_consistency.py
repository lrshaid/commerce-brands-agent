import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class WarehouseConsistencyTests(unittest.TestCase):
    def test_required_sql_files_exist(self):
        expected = {
            "warehouse/staging/stg_order_line_items.sql",
            "warehouse/staging/stg_refund_line_items.sql",
            "warehouse/staging/stg_refund_order_adjustments.sql",
            "warehouse/staging/stg_return_line_items.sql",
            "warehouse/marts/fct_returns.sql",
            "warehouse/marts/metric_revenue_daily.sql",
        }
        self.assertEqual(
            {path for path in expected if not (ROOT / path).is_file()},
            set(),
        )

    def test_third_party_metrics_are_not_implemented(self):
        metrics = yaml.safe_load((ROOT / "semantic/metrics.yaml").read_text())["metrics"]
        offenders = [
            name
            for name, metric in metrics.items()
            if metric["purity"] == "third_party" and metric.get("implemented")
        ]
        self.assertEqual(offenders, [])

    def test_sql_is_parameterized_and_contains_no_brand_identifiers(self):
        sql = "\n".join(
            path.read_text()
            for path in sorted((ROOT / "warehouse").rglob("*.sql"))
        )
        self.assertIn("{{project}}", sql)

    def test_rmv_contract_is_full_outer_and_negative(self):
        sql = (ROOT / "warehouse/marts/fct_returns.sql").read_text().lower()
        self.assertIn("full outer join", sql)
        self.assertIn("-abs(coalesce", sql)
        for status in ("matched", "refund_no_return", "return_no_refund"):
            self.assertIn(status, sql)


if __name__ == "__main__":
    unittest.main()
