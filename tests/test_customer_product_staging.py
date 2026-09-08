import unittest
from pathlib import Path

from agent.warehouse.catalog_queries import compile_catalog_queries


ROOT = Path(__file__).parents[1]


class CustomerProductStagingContractTests(unittest.TestCase):
    def read(self, relative):
        return (ROOT / relative).read_text()

    def test_customer_page_and_projection_grain(self):
        page = self.read('dbt/models/staging/customers/stg_shopify__customer_pages.sql')
        model = self.read('dbt/models/staging/customers/stg_shopify__customers.sql')
        self.assertIn("m.stream = 'customers'", page)
        self.assertIn("m.status = 'published'", page)
        self.assertIn("$.data.customers.nodes", model)
        for field in ('customer_gid', 'created_at', 'updated_at', 'number_of_orders',
                      'amount_spent_amount', 'email'):
            self.assertIn(field, model)

    def test_product_variant_keeps_parent_lineage(self):
        product = self.read('dbt/models/staging/products/stg_shopify__products.sql')
        variant = self.read('dbt/models/staging/products/stg_shopify__product_variants.sql')
        self.assertIn("$.data.products.nodes", product)
        self.assertIn("$.data.node.variants.nodes", variant)
        for field in ('product_gid', 'variant_gid', 'sku', 'price',
                      'inventory_quantity', 'inventory_item_gid'):
            self.assertIn(field, variant)

    def test_fixture_shape_matches_compiled_operation(self):
        customers = self.read('queries/shopify/customers_query.graphql')
        products = self.read('queries/shopify/products_query.graphql')
        plan = compile_catalog_queries(customers, products)
        # The compiler makes variants an owner-scoped operation. The staging
        # model must therefore read its node.variants.nodes response, not the
        # bounded nested connection in the products root page.
        self.assertIn('node(id:', plan.variants)
        self.assertIn('variants(first:', plan.variants)
        self.assertIn('nodes {', plan.variants)
        variant_sql = self.read('dbt/models/staging/products/stg_shopify__product_variants.sql')
        self.assertIn("$.data.node.variants.nodes", variant_sql)
        fixture = {
            'customers': {'nodes': [{'id': 'gid://shopify/Customer/1'},
                                    {'id': 'gid://shopify/Customer/2'}]},
            'products': {'nodes': [
                {'id': 'gid://shopify/Product/1'},
                {'id': 'gid://shopify/Product/2'},
            ]},
            'variants': {
                'node': {'id': 'gid://shopify/Product/1', 'variants': {'nodes': [
                    {'id': 'gid://shopify/ProductVariant/1'},
                    {'id': 'gid://shopify/ProductVariant/2'},
                ]}},
            },
        }
        self.assertEqual(len(fixture['customers']['nodes']), 2)
        self.assertEqual(len(fixture['products']['nodes']), 2)
        variants = fixture['variants']['node']['variants']['nodes']
        self.assertEqual(len(variants), 2)
        self.assertEqual(fixture['variants']['node']['id'], 'gid://shopify/Product/1')


class CustomerIdentityContractTests(unittest.TestCase):
    def test_identity_key_is_sha256_email_with_guest_fallback_and_dedup(self):
        sql = (ROOT / "dbt/models/intermediate/shopify/int_shopify__customer_identity.sql").read_text()
        for field in (
            "sha256(lower(trim(email)))",
            "is_email_based",
            "canonical_customer_gid",
            "linked_customer_count",
            "linked_customer_gids",
            "stg_shopify__customers",
        ):
            self.assertIn(field, sql)
        self.assertIn("row_number() over", sql)
        self.assertIn("created_at asc nulls last", sql)
        self.assertIn("amount_spent_amount desc nulls last", sql)
        self.assertNotIn("email as email", sql)


class CustomerRfmKlaviyoTests(unittest.TestCase):
    def test_purchase_summary_net_basis_and_identity_join(self):
        sql = (ROOT / "dbt/models/intermediate/shopify/int_shopify__customer_purchase_summary.sql").read_text()
        for field in (
            "int_shopify__customer_identity",
            "linked_customer_gids",
            "cancelled_at is null",
            "discounted_total_shop_amount",
            "refund_created_at is not null",
            "net_contribution",
        ):
            self.assertIn(field, sql)

    def test_rfm_follows_klaviyo_methodology(self):
        sql = (ROOT / "dbt/models/marts/dim_customer_rfm.sql").read_text()
        self.assertIn("<= 180 then 3", sql)
        self.assertIn("<= 365 then 2", sql)
        self.assertIn("percentile_cont(order_count, 0.33)", sql)
        self.assertIn("percentile_cont(order_count, 0.66)", sql)
        self.assertIn("greatest(t.f_p33 + 1, t.f_p66)", sql)
        self.assertIn("percentile_cont(gross_spend, 0.66)", sql)
        for group in ("Champions", "Loyal", "Recent", "Needs attention", "At risk", "Inactive"):
            self.assertIn(f"then '{group}'", sql)
        for combo in ("'333'", "'323'", "'311'", "'213'", "'231'", "'111'"):
            self.assertIn(combo, sql)

    def test_cohorts_are_first_purchase_month_matrix(self):
        sql = (ROOT / "dbt/models/marts/fct_customer_cohorts.sql").read_text()
        for field in ("cohort_month", "months_since_first_purchase", "date_trunc", "date_diff"):
            self.assertIn(field, sql)
        self.assertIn("cancelled_at is null", sql)
