"""Execute the dbt return fact and daily mart with local SQL fixtures."""
import re
import sqlite3
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def model_sql(name):
    sql = (ROOT / 'dbt/models/marts' / f'{name}.sql').read_text()
    sql = re.sub(r'\{\{\s*config\(.*?\)\s*\}\}', '', sql)
    sql = sql.replace('current_timestamp()', 'CURRENT_TIMESTAMP')
    return re.sub(r"\{\{\s*ref\('([^']+)'\)\s*\}\}", r'\1', sql)


class ReturnUnitSignTest(unittest.TestCase):
    def test_returns_reduce_net_units_and_keep_refund_precedence(self):
        # SQLite executes these models' joins, arithmetic and aggregations.
        # SAFE_DIVIDE is the only BigQuery-specific function used by the fact.
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.create_function('safe_divide', 2, lambda a, b: a / b if a is not None and b else None)
        db.executescript('''
          CREATE TABLE int_refund_lines_by_order_line (
            shop_key, order_gid, order_line_item_id, refund_subtotal_amount,
            refund_tax_amount, refunded_quantity, latest_refund_created_at);
          INSERT INTO int_refund_lines_by_order_line VALUES
            ('shop', 'sale', 'line', 20, 0, 2, '2025-01-01'),
            ('shop', 'negative', 'negative', 10, 0, -1, '2025-01-02'),
            ('shop', 'cancelled', 'cancelled', 30, 0, 3, '2025-01-03');
          CREATE TABLE int_return_lines_by_order_line (
            shop_key, order_gid, order_line_item_id, return_subtotal_amount,
            return_tax_amount, returned_quantity);
          INSERT INTO int_return_lines_by_order_line VALUES
            ('shop', 'sale', 'line', 20, 0, 5),
            ('shop', 'pending', 'pending', 0, 0, 4);
          CREATE TABLE int_refund_adjustments_by_order (shop_key, order_gid, adjustment_amount);
          CREATE TABLE int_shopify__orders (shop_key, order_gid, processed_at, cancelled_at);
          INSERT INTO int_shopify__orders VALUES
            ('shop', 'sale', '2025-01-01', NULL),
            ('shop', 'negative', '2025-01-02', NULL),
            ('shop', 'cancelled', '2025-01-03', '2025-01-03'),
            ('shop', 'pending', '2025-01-04', NULL);
          CREATE TABLE int_shopify__order_line_items (
            shop_key, order_gid, discounted_total_shop_amount, quantity);
          INSERT INTO int_shopify__order_line_items VALUES
            ('shop', 'sale', 100, 10), ('shop', 'negative', 100, 10),
            ('shop', 'cancelled', 100, 10), ('shop', 'pending', 100, 10);
        ''')
        db.execute('CREATE TABLE fct_returns AS ' + model_sql('fct_returns'))
        quantities = dict(db.execute('SELECT order_gid, returned_quantity FROM fct_returns'))
        self.assertEqual(quantities, {'sale': -2, 'negative': -1, 'cancelled': -3, 'pending': -4})
        db.execute('CREATE TABLE metric_revenue_daily AS ' + model_sql('metric_revenue_daily'))
        rows = db.execute('SELECT metric_date, gross_units, returned_units, net_units FROM metric_revenue_daily ORDER BY metric_date').fetchall()
        self.assertEqual(rows, [
            ('2025-01-01', 10, -2, 8),
            ('2025-01-02', 10, -1, 9),
            ('2025-01-03', 0, 0, 0),
            ('2025-01-04', 10, 0, 10),
        ])
