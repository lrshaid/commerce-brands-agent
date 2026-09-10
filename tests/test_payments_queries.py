import json
import unittest
from pathlib import Path

from agent.warehouse.payments_queries import PaymentsProjectionError, compile_payments_queries


ROOT = Path(__file__).resolve().parents[1]
TENDER = (ROOT / "queries/shopify/tender_transactions_bulk.graphql").read_text()
BALANCE = (ROOT / "queries/shopify/balance_transactions_bulk.graphql").read_text()
DISPUTES = (ROOT / "queries/shopify/disputes_bulk.graphql").read_text()


class PaymentsQueryTests(unittest.TestCase):
    def test_compiles_three_read_only_paginated_operations(self):
        plan = compile_payments_queries(TENDER, BALANCE, DISPUTES)
        self.assertEqual(len(plan.documents()), 3)
        for document in plan.documents():
            self.assertIn("pageInfo", document)
            self.assertIn("after:", document)
            self.assertNotIn("mutation", document.lower())
        self.assertIn("TenderTransactionsPage", plan.tender_transactions)
        self.assertIn("shopifyPaymentsAccount", plan.balance_transactions)
        self.assertIn("DisputesPage", plan.disputes)

    def test_preserves_node_projection_from_sources(self):
        plan = compile_payments_queries(TENDER, BALANCE, DISPUTES)
        self.assertIn("paymentMethod", plan.tender_transactions)
        self.assertIn("remoteReference", plan.tender_transactions)
        self.assertIn("associatedPayout", plan.balance_transactions)
        self.assertIn("networkReasonCode", plan.disputes)
        self.assertNotIn("cursor", plan.tender_transactions)

    def test_rejects_mutations_and_changed_projections(self):
        broken = [(TENDER.replace("query TenderTransactionsBulk", "mutation TenderTransactionsBulk"), BALANCE, DISPUTES),
                  (TENDER, BALANCE + BALANCE, DISPUTES),
                  (TENDER, BALANCE.replace("transactionDate", "transactionAt"), DISPUTES),
                  (TENDER, BALANCE, DISPUTES.replace("legacyResourceId", "legacyResource"))]
        for tender, balance, disputes in broken:
            with self.assertRaises(PaymentsProjectionError):
                compile_payments_queries(tender, balance, disputes)

    def test_rejects_tender_root_without_explicit_query_variable(self):
        with self.assertRaises(PaymentsProjectionError):
            compile_payments_queries(TENDER.replace("(query: $query)", "(first: 50)"), BALANCE, DISPUTES)


if __name__ == "__main__":
    unittest.main()
