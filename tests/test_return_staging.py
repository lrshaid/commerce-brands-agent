import json
import unittest
from pathlib import Path

from agent.warehouse.returns_queries import compile_return_queries
from infra.scripts.validate_return_staging import build_probe, validate_result


ROOT = Path(__file__).resolve().parents[1]


class ReturnStagingContractTests(unittest.TestCase):
    def test_compiler_emits_four_independently_paginated_operations(self):
        source = (ROOT / 'queries/shopify/return_line_items_bulk.graphql').read_text()
        plan = compile_return_queries(source)
        self.assertEqual(len(plan.documents()), 4)
        for document in plan.documents():
            self.assertIn('pageInfo', document)
            self.assertIn('after:', document)

    def test_models_preserve_only_projection_fields_and_lineage(self):
        models = ROOT / 'dbt/models/staging/returns'
        pages = (models / 'stg_shopify__return_pages.sql').read_text()
        returns = (models / 'stg_shopify__returns.sql').read_text()
        lines = (models / 'stg_shopify__return_line_items.sql').read_text()
        refunds = (models / 'stg_shopify__return_refunds.sql').read_text()
        self.assertIn("m.stream = 'returns'", pages)
        self.assertIn("m.transport = 'shopify_graphql_pages'", pages)
        self.assertIn("p.operation = 'returns'", returns)
        self.assertIn("p.operation = 'returnLineItems'", lines)
        self.assertIn("p.operation = 'refunds'", refunds)
        self.assertIn('r.order_gid', lines)
        self.assertIn('r.order_gid', refunds)
        self.assertNotIn('subtotal_amount', returns + lines + refunds)
        self.assertNotIn('tax_amount', returns + lines + refunds)

    def test_compiled_returns_models_use_analytics_schema(self):
        manifest_path = ROOT / 'dbt/target/manifest.json'
        self.assertTrue(manifest_path.is_file(), 'run dbt compile before checking relations')
        manifest = json.loads(manifest_path.read_text())
        expected = {
            'stg_shopify__return_pages',
            'stg_shopify__returns',
            'stg_shopify__return_line_items',
            'stg_shopify__return_refunds',
        }
        models = {
            node['name']: node
            for node in manifest['nodes'].values()
            if node.get('resource_type') == 'model' and node.get('name') in expected
        }
        self.assertEqual(set(models), expected)
        for model in models.values():
            self.assertEqual(model['config']['schema'], 'analytics')
            self.assertIn('.`analytics`.', model['relation_name'])

    def test_nonempty_multipage_probe_fixture_reconciles_without_fanout(self):
        sql, bodies, files = build_probe()
        self.assertEqual(len(bodies), 7)
        self.assertEqual(len(files), 7)
        self.assertIn('orphan_child_pages', sql)
        result = {'page_count': 7, 'return_count': 2, 'line_count': 2,
                  'refund_link_count': 2, 'empty_return_count': 1,
                  'missing_line_parents': 0, 'missing_refund_parents': 0,
                  'orphan_child_pages': 0}
        self.assertIs(validate_result(result), result)

    def test_missing_parent_and_count_mismatch_are_rejected(self):
        result = {'page_count': 7, 'return_count': 2, 'line_count': 2,
                  'refund_link_count': 2, 'empty_return_count': 1,
                  'missing_line_parents': 1, 'missing_refund_parents': 0,
                  'orphan_child_pages': 1}
        with self.assertRaisesRegex(RuntimeError, 'missing_line_parents'):
            validate_result(result)
        result['missing_line_parents'] = 0
        result['line_count'] = 3
        with self.assertRaisesRegex(RuntimeError, 'line_count'):
            validate_result(result)


if __name__ == '__main__':
    unittest.main()
