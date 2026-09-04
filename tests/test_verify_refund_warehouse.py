import copy
import unittest

from infra.scripts.verify_refund_warehouse import validate_result


def fixture():
    return {
        'manifest_count': 1, 'status': 'published', 'completion_seals': 1,
        'raw_count': 5, 'unique_physical_keys': 5, 'stg_page_count': 5,
        'manifest_raw_count': 5, 'bad_record_index': 0, 'raw_hash_mismatches': 0,
        'duplicate_raw_files': 0, 'raw_pages_without_manifest': 0,
        'manifest_pages_without_raw': 0, 'response_page_files': 5,
        'distinct_response_page_generations': 5, 'payload_order_count': 2,
        'manifest_root_count': 2, 'payload_refund_count': 1, 'stg_refund_count': 1,
        'payload_line_count': 2, 'stg_line_count': 2,
        'payload_transaction_count': 1, 'stg_transaction_count': 1,
        'payload_adjustment_count': 1, 'stg_adjustment_count': 1,
        'null_line_parent_gids': 0, 'null_transaction_parent_gids': 0,
        'null_adjustment_parent_gids': 0,
    }


class RefundWarehouseVerificationTests(unittest.TestCase):
    def test_valid_counts_and_hashes(self):
        self.assertEqual(validate_result(fixture())['manifest_count'], 1)

    def assert_invalid(self, key, value, message):
        row = fixture()
        row[key] = value
        with self.assertRaisesRegex(RuntimeError, message):
            validate_result(row)

    def test_page_omitted(self):
        self.assert_invalid('manifest_pages_without_raw', 1, 'manifest_pages_without_raw')

    def test_duplicate_response_page(self):
        self.assert_invalid('distinct_response_page_generations', 4, 'Duplicate response-page')

    def test_hash_mismatch(self):
        self.assert_invalid('raw_hash_mismatches', 1, 'raw_hash_mismatches')

    def test_manifest_multiplicity(self):
        self.assert_invalid('manifest_count', 2, 'manifest_count')

    def test_page_count_mismatch(self):
        row = copy.deepcopy(fixture())
        row['payload_line_count'] = 3
        with self.assertRaisesRegex(RuntimeError, 'payload_line_count'):
            validate_result(row)


if __name__ == '__main__':
    unittest.main()
