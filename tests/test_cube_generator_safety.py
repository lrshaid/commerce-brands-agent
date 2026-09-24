import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]


class GeneratorSafetyTest(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('cube_generator', ROOT / 'scripts/generate_cube_model.py')
        self.g = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.g)

    def test_blocked_ratio_or_dependency_is_not_public(self):
        for blocked in ('aov', 'gmv'):
            contract = yaml.safe_load(self.g.CONTRACT.read_text())
            contract['metrics'][blocked]['implementation_status'] = 'blocked'
            _, _, output, exposed = self.g.build_cube(
                'metric_revenue_daily', contract['marts']['metric_revenue_daily'], contract, {})
            measures = {m['name']: m for m in yaml.safe_load(output)['cubes'][0]['measures']}
            self.assertFalse(measures['aov']['public'])
            self.assertNotIn('aov', exposed)

    def test_obsolete_generated_files_fail_check_and_are_removed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cubes, views = root / 'cubes', root / 'views'
            cubes.mkdir()
            views.mkdir()
            old = cubes / 'old.yml'
            old.write_text(self.g.HEADER + 'cubes: []\n')
            with patch.multiple(self.g, REPO=root, OUT_CUBES=cubes, OUT_VIEWS=views), patch.object(self.g, 'generate', return_value={}), contextlib.redirect_stdout(io.StringIO()):
                with patch('sys.argv', ['generate', '--check']):
                    self.assertEqual(self.g.main(), 1)
                    self.assertTrue(old.exists())
                with patch('sys.argv', ['generate']):
                    self.assertEqual(self.g.main(), 0)
                    self.assertFalse(old.exists())

    def test_unmanaged_files_are_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cubes = root / 'cubes'
            cubes.mkdir()
            old = cubes / 'handwritten.yaml'
            old.write_text('cubes: []\n')
            with patch.multiple(self.g, REPO=root, OUT_CUBES=cubes, OUT_VIEWS=root / 'views'), patch.object(self.g, 'generate', return_value={}), patch('sys.argv', ['generate']), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(self.g.main(), 1)
                self.assertTrue(old.exists())
