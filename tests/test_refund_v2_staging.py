from jinja2 import Environment
import unittest
import json
import os
try:
    import duckdb
    import sqlglot
except ImportError:
    duckdb = None
from pathlib import Path
from datetime import datetime,timezone
from unittest.mock import patch
from tests import test_refunds_v2 as fixture_module
refund, connection = fixture_module.refund, fixture_module.connection
from agent.warehouse.refund_capture import encoded
from agent.warehouse.refund_raw_v2 import prepare_refund_raw_v2


@unittest.skipIf(duckdb is None, "Install tests/requirements-sql.txt to run local SQL fixtures")
class RefundStagingV2Tests(unittest.TestCase):
    def test_money_only_shipping_only_multiline_and_currency(self):
        root = Path(__file__).resolve().parents[1]
        previous = Path.cwd()
        os.chdir(root)
        self.addCleanup(os.chdir, previous)
        capture,args=fixture_module.RefundV2Tests().fixture(refunds=3)
        nodes=[refund(i) for i in range(3)]
        # Multi-line refund, money-only refund and shipping-only refund.
        nodes[0]['refundLineItems']=connection(count=2)
        for n in nodes[0]['refundLineItems']['nodes']:
         n.update(quantity=1,lineItem={'id':'gid://shopify/LineItem/1'},subtotalSet={'shopMoney':{'amount':'10.00','currencyCode':'CAD'}},totalTaxSet={'shopMoney':{'amount':'1.00','currencyCode':'CAD'}})
        for i in [0,1]:
         nodes[i]['transactions']=connection('OrderTransaction',1,start=i)
         nodes[i]['transactions']['nodes'][0].update(kind='REFUND',status='SUCCESS',amountSet={'shopMoney':{'amount':'12.00','currencyCode':'CAD'},'presentmentMoney':{'amount':'9.00','currencyCode':'USD'}},paymentId='pay-'+str(i))
        nodes[2]['refundShippingLines']=connection('RefundShippingLine',1)
        nodes[2]['refundShippingLines']['nodes'][0].update(subtotalAmountSet={'shopMoney':{'amount':'5.00','currencyCode':'CAD'},'presentmentMoney':{'amount':'3.00','currencyCode':'USD'}},taxAmountSet={'shopMoney':{'amount':'1.00','currencyCode':'CAD'},'presentmentMoney':{'amount':'0.60','currencyCode':'USD'}})
        with patch.object(capture,'_http',return_value=encoded({'data':{'nodes':nodes}})):
         capture.collect()
        prepared=prepare_refund_raw_v2(**args,ingested_at=datetime.now(timezone.utc))
        rows=list(prepared['records'])
        db=duckdb.connect()
        db.execute('create table shopify_refunds__order_refunds(shop_key varchar,extraction_id varchar,file_id varchar,record_index bigint,record_sha256 varchar,record_text varchar,ingested_at timestamptz,payload json)')
        for r in rows:
         db.execute('insert into shopify_refunds__order_refunds values (?,?,?,?,?,?,?,?)',[r[k] for k in ['shop_key','extraction_id','file_id','record_index','record_sha256','record_text','ingested_at','payload']])
        db.execute('create table shopify_refunds__ingestion_runs(shop_key varchar,extraction_id varchar,stream varchar,status varchar,transport varchar,published_at timestamptz,files json,raw_record_count bigint)')
        db.execute('insert into shopify_refunds__ingestion_runs values (?,?,?,?,?,?,?,?)',[args['shop_gid'],args['extraction_id'],'order_refunds','published','shopify_bulk_and_graphql_pages_v2',datetime.now(timezone.utc),json.dumps(prepared['files']),prepared['raw_record_count']])
        env=Environment()
        env.globals.update(source=lambda s,t:s+'__'+t,ref=lambda n:n,config=lambda **kw:'')
        macros='\n'.join(p.read_text() for p in Path('dbt/macros').glob('*.sql') if p.name in ['shopify_refund_pages.sql','shopify_refund_child_nodes.sql','shopify_types.sql'])
        def render(path):
         sql=env.from_string(macros+'\n'+path.read_text()).render()
         return sqlglot.transpile(sql,read='bigquery',write='duckdb')[0]
        for name in ['refunds','refund_line_items','refund_transactions','refund_shipping_lines','refund_adjustments','refund_return_lines','refund_exchange_lines','refund_returns']:
         path=Path('dbt/models/staging/refunds/stg_shopify__'+name+'.sql')
         try:
          db.execute('create view stg_shopify__'+name+' as '+render(path))
          db.execute('select count(*) from stg_shopify__'+name).fetchone()
         except Exception as e:
          print(render(path))
          raise
        for path in [Path('dbt/models/intermediate/shopify/int_shopify__refunds.sql'),Path('dbt/models/intermediate/returns/int_refund_adjustments_by_order.sql')]:
         db.execute('create view '+path.stem+' as '+render(path))
        for name in ['refund_child_counts','refund_shipping_adjustments','reconcile_int_shopify_refunds','refund_page_publication_counts','refund_page_uniqueness']:
         result=db.execute(render(Path('dbt/tests/'+name+'.sql'))).fetchall()
         assert not result,(name,result)

        assert db.execute('select count(*) from stg_shopify__refund_transactions').fetchone()[0]==2
        assert db.execute('select adjustment_amount from int_refund_adjustments_by_order').fetchone()[0]==-5
        assert db.execute('select presentment_amount from stg_shopify__refund_adjustments where is_synthetic').fetchone()[0]==-3
        db.close()
