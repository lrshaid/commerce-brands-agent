import unittest

from agent.warehouse.raw_publication import publication_sql, publish_records


class RawPublicationTests(unittest.TestCase):
    def test_sql_has_guard_and_atomic_commit(self):
        sql = publication_sql('commerce-agents-dev.platform_smoke', 'acceptance', '_load_' + 'a'*32)
        self.assertLess(sql.index('BEGIN TRANSACTION'), sql.index('UPDATE'))
        self.assertLess(sql.index('Conflicting replay record'), sql.index('INSERT INTO'))
        self.assertTrue(sql.strip().endswith('COMMIT TRANSACTION;'))
        self.assertNotIn('DELETE FROM', sql)
        self.assertIn('PARSE_JSON(payload)', sql)

    def test_invalid_identifiers_and_blocked_exchange_rejected(self):
        for dataset, stream, stage in [('x`;DROP', 'orders', '_load_'+'a'*32),
                                       ('commerce-agents-dev.raw_shopify', 'exchanges', '_load_'+'a'*32),
                                       ('commerce-agents-dev.raw_shopify', 'orders', 'bad')]:
            with self.assertRaises(ValueError):
                publication_sql(dataset, stream, stage)

    def test_no_transport_validation_no_publication(self):
        with self.assertRaises(ValueError):
            publish_records(None, 'commerce-agents-dev.platform_smoke', 'acceptance', [], {})


if __name__ == '__main__':
    unittest.main()
