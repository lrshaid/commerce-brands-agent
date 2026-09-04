import os
import unittest
from unittest.mock import patch

from infra.scripts.verify_returns_warehouse import validate_result, main


def fixture():
    return {'manifest_count': 1, 'status': 'published', 'transport': 'shopify_graphql_pages',
            'completion_seals': 1, 'bad_record_index': 0, 'raw_hash_mismatches': 0,
            'duplicate_raw_files': 0, 'raw_pages_without_manifest': 0,
            'manifest_pages_without_raw': 0, 'null_line_parents': 0, 'null_refund_parents': 0,
            'orphan_line_parents': 0, 'orphan_refund_parents': 0, 'response_page_files': 7,
            'distinct_response_page_generations': 7, 'raw_count': 7, 'unique_physical_keys': 7,
            'stg_page_count': 7, 'manifest_raw_count': 7, 'payload_order_count': 1,
            'manifest_root_count': 1, 'payload_return_count': 2, 'stg_return_count': 2,
            'payload_line_count': 2, 'stg_line_count': 2, 'payload_refund_count': 2,
            'stg_refund_count': 2}


class ReturnsWarehouseVerifierTests(unittest.TestCase):
    def test_valid_fixture(self):
        self.assertEqual(validate_result(fixture())['payload_return_count'], 2)

    def test_adversarial_omitted_duplicate_hash_count_orphan(self):
        cases = [('manifest_pages_without_raw', 1), ('distinct_response_page_generations', 6),
                 ('raw_hash_mismatches', 1), ('line_count', 3), ('orphan_line_parents', 1)]
        for key, value in cases:
            row = fixture()
            if key == 'line_count':
                row['stg_line_count'] = value
            else:
                row[key] = value
            with self.assertRaises(RuntimeError):
                validate_result(row)

    def test_empty_or_missing_token_refuses_adc(self):
        with patch.dict(os.environ, {'GOOGLE_OAUTH_ACCESS_TOKEN': ''}, clear=False), \
             patch('sys.argv', ['verify_returns_warehouse.py', '--extraction-id', 'x', '--shop-gid', 'y']):
            with self.assertRaisesRegex(RuntimeError, 'refusing ADC fallback'):
                main()


if __name__ == '__main__':
    unittest.main()
