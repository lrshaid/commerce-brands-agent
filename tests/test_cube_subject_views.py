"""Check topic isolation, disabled sources, documented queries and ratio semantics."""
import json
import re
import runpy
import sqlite3
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


class SubjectViewsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.generator = runpy.run_path(str(ROOT / 'scripts/generate_cube_model.py'))
        cls.contract = yaml.safe_load((ROOT / 'semantic/serving_contract.yaml').read_text())
        cls.files = cls.generator['generate']()

    def test_documented_queries_reference_public_members(self):
        allowed = set()
        for content in self.files.values():
            for view in yaml.safe_load(content).get('views', []):
                for cube in view['cubes']:
                    allowed.update(f"{view['name']}.{m}" for m in cube['includes'])
        text = (ROOT / 'semantic/contract.md').read_text()
        for block in re.findall(r'```json\n(.*?)\n```', text, re.S):
            q = json.loads(block)
            members = q.get('measures', []) + q.get('dimensions', [])
            members += [t['dimension'] for t in q.get('timeDimensions', [])]
            members += [f['member'] for f in q.get('filters', [])]
            self.assertFalse(set(members) - allowed)

    def test_opt_in_and_private_sources(self):
        self.assertFalse(any('digital_funnel' in p for p in self.files))
        for path, content in self.files.items():
            if '/subject_' not in path:
                continue
            cube = yaml.safe_load(content)['cubes'][0]
            self.assertFalse(cube['public'])
            self.assertNotIn('joins', cube)
            self.assertNotIn('pre_aggregations', cube)
            self.assertNotIn('extraction_id', [d['name'] for d in cube['dimensions']])
        for name in ('customers', 'customer_ltv', 'customer_cohorts'):
            self.assertNotIn('shop_key', self.contract['subject_views'][name]['dimensions'])

    def test_ratios_use_population_totals_not_average_customer_ratios(self):
        spec = self.contract['subject_views']['customers']
        cube, _ = self.generator['build_subject']('customers', spec)
        measures = {m['name']: m for m in yaml.safe_load(cube)['cubes'][0]['measures']}
        sql = measures['aov']['sql']
        for name in ('gross_spend', 'orders'):
            sql = sql.replace('{'+name+'}', 'SUM('+measures[name]['sql']+')')
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.execute('CREATE TABLE customers (gross_spend REAL, order_count INTEGER)')
        db.executemany('INSERT INTO customers VALUES (?, ?)', [(100, 1), (90, 9)])
        self.assertEqual(db.execute('SELECT '+sql+' FROM customers').fetchone()[0], 19)
        db.execute('DELETE FROM customers')
        db.execute('INSERT INTO customers VALUES (0, 0)')
        self.assertIsNone(db.execute('SELECT '+sql+' FROM customers').fetchone()[0])

    def test_cross_topic_ratio_is_rejected(self):
        spec = dict(self.contract['subject_views']['customers'])
        spec['measures'] = {'bad': {'numerator': 'revenue.gmv', 'denominator': 'orders'}}
        with self.assertRaises(ValueError):
            self.generator['build_subject']('bad', spec)
