"""Execute production BigQuery model SQL, transpiled to DuckDB, on fixtures.

Install tests/requirements-sql.txt. This suite needs no cloud credentials.
"""
import unittest
from pathlib import Path

import duckdb
import jinja2
import sqlglot

ROOT = Path(__file__).resolve().parents[1]
MODELS = [
    'int_crm__events', 'int_crm__profile_identity', 'int_crm__messages',
    'int_crm__order_value', 'fct_crm_event', 'fct_crm_message_engagement',
    'metric_crm_campaign_performance', 'metric_crm_activity_daily',
    'fct_crm_order_attribution', 'metric_crm_attribution_daily',
    'dim_customer_crm', 'metric_crm_customer_engagement',
]


def render(name):
    path = next((ROOT / 'dbt/models').rglob(name + '.sql'))
    sql = jinja2.Environment().from_string(path.read_text()).render(
        config=lambda **kw: '', ref=lambda name: name)
    tree = sqlglot.parse_one(sql, read='bigquery')
    # BigQuery COUNTIF returns zero for all-NULL input; DuckDB COUNT_IF returns
    # NULL. Use equivalent COUNT(CASE) to preserve production semantics.
    def count_if(node):
        if isinstance(node, sqlglot.exp.CountIf):
            return sqlglot.exp.Count(this=sqlglot.exp.Case(ifs=[
                sqlglot.exp.If(this=node.this, true=sqlglot.exp.Literal.number(1))]))
        return node
    return tree.transform(count_if).sql(dialect='duckdb')


class CrmModelsTest(unittest.TestCase):
    def setUp(self):
        self.db = duckdb.connect()
        self.addCleanup(self.db.close)
        self.db.execute('''CREATE TABLE stg_klaviyo__events (
            shop_key VARCHAR, event_id VARCHAR, uuid VARCHAR, event_key VARCHAR,
            extraction_id VARCHAR, profile_id VARCHAR, email VARCHAR, datetime TIMESTAMP,
            event_type VARCHAR, metric_id VARCHAR, unknown_metric_id BOOLEAN,
            message VARCHAR, campaign VARCHAR, flow_id VARCHAR, campaign_name VARCHAR,
            message_name VARCHAR, subject VARCHAR, variant VARCHAR, method VARCHAR,
            ingested_at TIMESTAMP, published_at TIMESTAMP)''')
        self.db.execute('''CREATE TABLE stg_klaviyo__campaign_messages (
            shop_key VARCHAR, message_id VARCHAR, campaign_id VARCHAR, name VARCHAR,
            updated_at TIMESTAMP, published_at TIMESTAMP, ingested_at TIMESTAMP, message_key VARCHAR)''')
        self.db.execute('''CREATE TABLE stg_klaviyo__campaigns (
            shop_key VARCHAR, campaign_id VARCHAR, name VARCHAR, updated_at TIMESTAMP,
            published_at TIMESTAMP, ingested_at TIMESTAMP, campaign_key VARCHAR)''')
        self.db.execute('''CREATE TABLE int_shopify__orders (
            shop_key VARCHAR, order_gid VARCHAR, processed_at TIMESTAMP,
            cancelled_at TIMESTAMP, email VARCHAR)''')
        self.db.execute('''CREATE TABLE int_shopify__order_line_items (
            shop_key VARCHAR, order_gid VARCHAR, discounted_total_shop_amount DOUBLE,
            discounted_total_shop_currency VARCHAR)''')
        self.db.execute('''CREATE TABLE fct_returns (
            shop_key VARCHAR, order_gid VARCHAR, rmv_merchandise_amount DOUBLE,
            rmv_recognition_ts_utc TIMESTAMP)''')

    def event(self, eid, kind, ts, shop='a', profile='p', email='buyer@example.test',
              message='m', campaign='c', observed='2025-02-01', event_id=True):
        vals = [shop, eid if event_id else None, None, eid + observed, observed,
                profile, email, ts, kind, 'metric', kind is None, message, campaign,
                None, 'Campaign', 'Message', 'Subject', None, None, observed, observed]
        self.db.execute('INSERT INTO stg_klaviyo__events VALUES ('+','.join('?' for _ in vals)+')', vals)

    def order(self, oid, ts, shop='a', email='buyer@example.test', cancelled=False, amount=100, currency='USD'):
        self.db.execute('INSERT INTO int_shopify__orders VALUES (?, ?, ?, ?, ?)',
                        [shop, oid, ts, ts if cancelled else None, email])
        self.db.execute('INSERT INTO int_shopify__order_line_items VALUES (?, ?, ?, ?)',
                        [shop, oid, amount, currency])

    def build(self):
        for name in MODELS:
            self.db.execute('CREATE OR REPLACE TABLE '+name+' AS '+render(name))
        for path in (ROOT / 'dbt/tests/crm').glob('*.sql'):
            sql = jinja2.Environment().from_string(path.read_text()).render(ref=lambda name: name)
            translated = sqlglot.transpile(sql, read='bigquery', write='duckdb')[0]
            self.assertEqual(self.db.execute(translated).fetchall(), [], path.name)

    def test_replay_shop_isolation_repeat_deliveries_and_orphans(self):
        self.event('send', 'received-email', '2025-01-01 23:50:00')
        self.event('send', 'received-email', '2025-01-01 23:50:00', observed='2025-02-02')
        self.event('send', 'received-email', '2025-01-01 23:50:00', shop='b')
        self.event('open1', 'opened-email', '2025-01-02 00:00:00')
        self.event('open2', 'opened-email', '2025-01-02 00:01:00')
        self.event('click1', 'clicked-email', '2025-01-02 00:02:00')
        self.event('send2', 'received-email', '2025-01-03 00:00:00')
        self.event('click2', 'clicked-email', '2025-01-03 00:02:00')
        self.event('orphan', 'clicked-email', '2025-01-03 01:00:00', message='unknown')
        self.event('no-key', 'received-email', '2025-01-03 02:00:00', message=None)
        self.build()
        self.assertEqual(self.db.execute('SELECT count(*) FROM fct_crm_event').fetchone()[0], 9)
        rows = self.db.execute("SELECT delivery_date,open_events,click_events FROM fct_crm_message_engagement WHERE shop_key='a' AND message_id='m' ORDER BY delivery_ts").fetchall()
        self.assertEqual([(str(d),o,c) for d,o,c in rows], [('2025-01-01',2,1),('2025-01-03',0,1)])
        self.assertEqual(self.db.execute("SELECT click_events FROM fct_crm_message_engagement WHERE shop_key='b'").fetchone()[0], 0)
        self.assertIsNone(self.db.execute('SELECT open_rate FROM metric_crm_campaign_performance WHERE message_id IS NULL').fetchone()[0])
        self.assertEqual(self.db.execute("SELECT sum(event_count) FROM metric_crm_activity_daily WHERE event_type='clicked-email'").fetchone()[0],3)

    def test_one_touch_can_win_two_orders_with_exact_boundaries(self):
        self.event('send', 'received-email', '2025-01-01 00:00:00')
        self.event('click', 'clicked-email', '2025-01-01 00:01:00')
        for oid,ts in [('early','00:02:59'),('first','00:03:00'),('second','01:00:00'),('edge','06:00:00'),('late','06:00:01')]:
            self.order(oid,'2025-01-01 '+ts)
        self.order('cancelled','2025-01-01 01:00:00',cancelled=True)
        self.order('other-shop','2025-01-01 01:00:00',shop='b')
        self.build()
        rows=dict(self.db.execute("SELECT order_gid,attribution_status FROM fct_crm_order_attribution WHERE attribution_model='delivery_6h'").fetchall())
        self.assertEqual(rows['first'],'attributed')
        self.assertEqual(rows['second'],'attributed')
        self.assertEqual(rows['edge'],'attributed')
        for k in ('early','late'): self.assertEqual(rows[k],'no_eligible_touch')
        self.assertEqual(rows['other-shop'], 'attributed')
        self.assertNotIn('cancelled',rows)
        self.assertEqual(self.db.execute('SELECT count(*) FROM dim_customer_crm').fetchone()[0], 1)
        self.assertEqual(self.db.execute('SELECT order_count FROM dim_customer_crm').fetchone()[0], 6)
        self.assertEqual(self.db.execute("SELECT sum(attributed_value) FROM fct_crm_order_attribution WHERE attribution_model='delivery_6h'").fetchone()[0],400)
        self.assertEqual(self.db.execute('SELECT count(*) FROM fct_crm_order_attribution').fetchone()[0],12)

    def test_ambiguous_identity_prospects_unknown_types_and_currency(self):
        self.event('c1','clicked-email','2025-01-01',profile='amb',email='one@example.test')
        self.event('c2','clicked-email','2025-01-01',profile='amb',email='two@example.test')
        self.event('prospect','received-email','2025-01-01',profile='prospect',email='prospect@example.test')
        self.event('unknown',None,'2025-01-01',profile=None,email=None,event_id=False)
        self.order('amb-order','2025-01-01 01:00:00',email='one@example.test')
        self.build()
        self.assertEqual(self.db.execute("SELECT attribution_status FROM fct_crm_order_attribution WHERE attribution_model='click_6h'").fetchone()[0],'no_eligible_touch')
        self.assertEqual(self.db.execute("SELECT count(*) FROM dim_customer_crm WHERE customer_status='Prospect'").fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT sum(unknown_type_events) FROM metric_crm_activity_daily').fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT sum(missing_provider_id_events) FROM metric_crm_activity_daily').fetchone()[0],1)
        self.assertNotIn('email',[c[0] for c in self.db.execute('SELECT * FROM dim_customer_crm').description])

    def test_multicurrency_value_is_not_silently_summed(self):
        self.event('click','clicked-email','2025-01-01')
        self.order('mixed','2025-01-01 01:00:00')
        self.db.execute("INSERT INTO int_shopify__order_line_items VALUES ('a','mixed',20,'EUR')")
        self.build()
        self.assertIsNone(self.db.execute("SELECT attributed_value FROM fct_crm_order_attribution WHERE attribution_model='click_6h'").fetchone()[0])
        self.assertEqual(self.db.execute("SELECT missing_value_orders FROM metric_crm_attribution_daily WHERE attribution_model='click_6h'").fetchone()[0],1)
        self.assertIsNone(self.db.execute('SELECT observed_net_value FROM dim_customer_crm').fetchone()[0])

    def test_late_arrivals_update_old_delivery_and_future_click_never_wins(self):
        self.event('send','received-email','2025-01-01')
        self.event('future','clicked-email','2025-01-01 02:00:00')
        self.order('o','2025-01-01 01:00:00',email=' BUYER@example.test ')
        self.build()
        self.assertEqual(self.db.execute("SELECT attribution_status FROM fct_crm_order_attribution WHERE attribution_model='click_6h'").fetchone()[0],'no_eligible_touch')
        self.event('late','clicked-email','2025-01-01 00:30:00',observed='2025-03-01')
        self.build()
        self.assertEqual(self.db.execute("SELECT attribution_status FROM fct_crm_order_attribution WHERE attribution_model='click_6h'").fetchone()[0],'attributed')
        self.assertEqual(self.db.execute('SELECT click_events FROM fct_crm_message_engagement').fetchone()[0],2)

    def test_same_time_touches_tie_break_deterministically_and_metadata_does_not_fanout(self):
        self.event('a','clicked-email','2025-01-01')
        self.event('b','clicked-email','2025-01-01')
        self.order('o','2025-01-01 01:00:00')
        for day in ('2025-01-01','2025-01-02'):
            self.db.execute("INSERT INTO stg_klaviyo__campaign_messages VALUES ('a','m','c','Name',?,?,?,?)",[day,day,day,day])
            self.db.execute("INSERT INTO stg_klaviyo__campaigns VALUES ('a','c','Campaign',?,?,?,?)",[day,day,day,day])
        self.build()
        winner=self.db.execute("SELECT touch_event_key FROM fct_crm_order_attribution WHERE attribution_model='click_6h'").fetchone()[0]
        self.assertEqual(winner,self.db.execute('SELECT max(crm_event_key) FROM fct_crm_event').fetchone()[0])
        self.assertEqual(self.db.execute('SELECT count(*) FROM fct_crm_event').fetchone()[0],2)
        self.assertEqual(self.db.execute('SELECT count(*) FROM int_crm__messages').fetchone()[0],1)


if __name__ == '__main__':
    unittest.main()
