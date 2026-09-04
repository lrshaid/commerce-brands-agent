from dataclasses import replace
from datetime import datetime, timezone
import io
import unittest

from google.api_core.exceptions import PreconditionFailed

from agent.warehouse.raw_landing import ReplayConflict, land_jsonl
from agent.warehouse.raw_records import ExtractionIdentity, InvalidRawRecord


class Blob:
    def __init__(self, bucket, name):
        self.bucket, self.name = bucket, name
        self.generation = None

    def upload_from_file(self, source, **kwargs):
        assert kwargs['if_generation_match'] == 0
        if self.name in self.bucket.objects:
            raise PreconditionFailed('exists')
        self.data = source.read()
        self.size = len(self.data)
        self.generation = 123
        self.bucket.objects[self.name] = self

    def download_to_file(self, target, **kwargs):
        assert kwargs['if_generation_match'] == self.generation
        target.write(self.data)


class Bucket:
    name = 'synthetic-bucket'

    def __init__(self):
        self.objects = {}

    def blob(self, name):
        return Blob(self, name)

    def get_blob(self, name):
        return self.objects.get(name)


class RawLandingTests(unittest.TestCase):
    def setUp(self):
        self.bucket = Bucket()
        self.identity = ExtractionIdentity('test-shop', 'op-1', 'pending', 'a'*64,
                                          'b'*64, '2026-04', datetime.now(timezone.utc))

    def land(self, value=b'{"id":"synthetic"}\n', identity=None, **kwargs):
        return land_jsonl(io.BytesIO(value), self.bucket, identity or self.identity, 'orders', **kwargs)

    def test_create_and_replay_preserve_generation(self):
        first = self.land()
        second = self.land(identity=replace(self.identity, ingested_at=datetime.now(timezone.utc)))
        self.assertFalse(first['replay'])
        self.assertTrue(second['replay'])
        self.assertEqual(first['file_id'], second['file_id'])
        self.assertEqual(len(self.bucket.objects), 1)
        self.assertFalse(first['published'])

    def test_conflicting_content_and_query_do_not_overwrite(self):
        self.land()
        with self.assertRaises(ReplayConflict):
            self.land(b'{}')
        with self.assertRaises(ReplayConflict):
            self.land(identity=replace(self.identity, query_sha256='c'*64))
        self.assertEqual(next(iter(self.bucket.objects.values())).data, b'{"id":"synthetic"}\n')

    def test_bad_file_never_uploads_even_after_good_record(self):
        with self.assertRaises(InvalidRawRecord):
            self.land(b'{}\ninvalid')
        self.assertFalse(self.bucket.objects)

    def test_size_limit_before_upload(self):
        with self.assertRaises(ValueError):
            self.land(max_file_bytes=2)
        self.assertFalse(self.bucket.objects)

    def test_changed_stored_bytes_are_not_trusted(self):
        self.land()
        next(iter(self.bucket.objects.values())).data = b'{"id":"corrupted"}\n'
        with self.assertRaises(ReplayConflict):
            self.land()

    def test_new_operation_gets_new_object(self):
        self.land()
        self.land(identity=replace(self.identity, extraction_id='op-2'))
        self.assertEqual(len(self.bucket.objects), 2)


if __name__ == '__main__':
    unittest.main()
