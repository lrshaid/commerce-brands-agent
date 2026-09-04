from datetime import datetime, timezone
import hashlib
import io
import unittest

from agent.warehouse.raw_records import ExtractionIdentity, InvalidRawRecord, iter_raw_records


class RawRecordsTests(unittest.TestCase):
    def setUp(self):
        self.identity = ExtractionIdentity('synthetic-shop', 'extraction-1', 'generation-1',
                                         'a' * 64, 'b' * 64, '2026-04',
                                         datetime(2026, 9, 4, tzinfo=timezone.utc))

    def records(self, value, **kwargs):
        return list(iter_raw_records(io.BytesIO(value), self.identity, **kwargs))

    def test_exact_text_crlf_hash_and_missing_id(self):
        raw = b'{ "amount": 0.1234567890123456789, "quantity": 1 }'
        row = self.records(raw + b'\r\n')[0]
        self.assertEqual(row['record_text'], raw.decode())
        self.assertEqual(row['payload'], raw.decode())
        self.assertEqual(row['record_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertIsNone(row['object_gid'])
        self.assertIsNone(row['parent_gid'])

    def test_parent_is_explicit_not_previous_record(self):
        rows = self.records(b'{"id":"root"}\n{"id":"child","__parentId":"other-root"}\n{}')
        self.assertEqual(rows[1]['parent_gid'], 'other-root')
        self.assertIsNone(rows[2]['parent_gid'])
        self.assertEqual([r['record_index'] for r in rows], [1, 2, 3])

    def test_empty_file_is_empty_not_an_error(self):
        self.assertEqual(self.records(b''), [])

    def test_same_result_replay_is_identical(self):
        self.assertEqual(self.records(b'{"id":"synthetic"}\n'),
                         self.records(b'{"id":"synthetic"}\n'))

    def test_invalid_payloads_fail_without_leaking_customer_data(self):
        for value in (b'\n', b'[]', b'null', b'{"email":"private",}',
                      b'{"id":"x","id":"y"}', b'{"id":123}', b'{"n":NaN}', b'{"x":"\xff"}'):
            with self.subTest(value=value):
                with self.assertRaises(InvalidRawRecord) as error:
                    self.records(value)
                self.assertNotIn('private', str(error.exception))

    def test_size_limit_and_partial_file_failure(self):
        with self.assertRaises(InvalidRawRecord):
            self.records(b'{"long":"text"}', max_record_bytes=5)
        with self.assertRaises(InvalidRawRecord):
            self.records(b'{}\nmalformed')

    def test_identity_requires_aware_timestamp(self):
        with self.assertRaises(ValueError):
            ExtractionIdentity('s', 'e', 'f', 'a'*64, 'b'*64, '2026-04', datetime(2026, 1, 1))


if __name__ == '__main__':
    unittest.main()
