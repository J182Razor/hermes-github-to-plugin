import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

class MetadataBounds(unittest.TestCase):
    def test_package_metadata_is_bounded_before_output(self):
        mod = load('package_bounds', 'generators/package_adapter.py')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'src'; root.mkdir()
            (root / 'pyproject.toml').write_bytes(b'#' * 200)
            out = Path(tmp) / 'out'
            with patch.object(mod, 'METADATA_LIMIT', 100, create=True), patch('subprocess.Popen', side_effect=AssertionError('spawn')), patch('socket.socket', side_effect=AssertionError('network')):
                with self.assertRaisesRegex(ValueError, 'too large'):
                    mod.generate(root, out, 'demo', {'type': 'python_pkg'})
            self.assertFalse(out.exists())

    def test_fixed_metadata_rejects_large_without_leaking_content(self):
        mod = load('fixed_bounds', 'generators/fixed_adapter.py')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'src'; root.mkdir()
            (root / 'pyproject.toml').write_bytes(b'PRIVATE_CANARY' * 20)
            out = Path(tmp) / 'out'
            with patch.object(mod, 'METADATA_LIMIT', 100, create=True):
                with self.assertRaises(ValueError) as ctx:
                    mod.generate(root, out, 'demo', {'type': 'python_pkg'})
            self.assertIn('too large', str(ctx.exception))
            self.assertNotIn('PRIVATE_CANARY', str(ctx.exception))
            self.assertFalse(out.exists())

    def test_detector_rejects_oversize_metadata(self):
        mod = load('detector_bounds', 'detector.py')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'package.json').write_bytes(b' ' * 200)
            with patch.object(mod, 'METADATA_LIMIT', 100, create=True):
                with self.assertRaisesRegex(ValueError, 'too large'):
                    mod.detect_type(str(root))
