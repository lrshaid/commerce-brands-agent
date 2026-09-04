import unittest
from decimal import Decimal

from infra.cost_report.report import query_for, summarize


class CostReportTests(unittest.TestCase):
    def test_missing_data_is_not_zero(self):
        result = summarize([])
        self.assertFalse(result['totals']['month_to_date']['available'])
        self.assertIsNone(result['totals']['month_to_date']['net_exported_cost'])

    def test_decimal_totals_and_distinct_periods(self):
        rows = [dict(period='month_to_date', currency='USD',
                     before_promotion_budget_basis=v, net_exported_cost='0')
                for v in ('0.1', '0.2')]
        result = summarize(rows)
        self.assertEqual(result['totals']['month_to_date']['before_promotion_budget_basis'], Decimal('0.3'))
        self.assertFalse(result['totals']['previous_week']['available'])

    def test_reject_currency_mismatch(self):
        with self.assertRaises(ValueError):
            summarize([{'currency': 'ARS'}])

    def test_table_identifier_cannot_inject_sql(self):
        for table in ('x', 'commerce-agents-dev.billing_export.foo`; DROP TABLE x;--'):
            with self.assertRaises(ValueError):
                query_for(table)
        sql = query_for('commerce-agents-dev.billing_export.gcp_billing_export_v1_015D02_62F1CD_5D6D2A')
        self.assertNotIn('__BILLING_TABLE__', sql)
        self.assertIn('project.id = @project_id', sql)


if __name__ == '__main__':
    unittest.main()
