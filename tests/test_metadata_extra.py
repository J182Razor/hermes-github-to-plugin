import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]

def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod

class MetadataExtra(unittest.TestCase):
    def test_actual_read_is_explicitly_limited(self):
        mod = load('metadata_check', 'metadata_io.py')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'data'; path.write_bytes(b'abc')
            real = mod.os.fdopen
            calls = []
            class Stream:
                def __init__(self, fd, mode): self.f = real(fd, mode)
                def __enter__(self): return self
                def __exit__(self, *a): self.f.close()
                def fileno(self): return self.f.fileno()
                def read(self, n): calls.append(n); return self.f.read(n)
            with patch.object(mod.os, 'fdopen', side_effect=Stream):
                self.assertEqual(mod.read_bounded(path, 4), b'abc')
            self.assertEqual(calls, [5])

    def test_positive_package_detection_and_generation_does_not_execute(self):
        detector = load('metadata_detector', 'detector.py')
        fixed = load('metadata_fixed', 'generators/fixed_adapter.py')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'src'; root.mkdir()
            marker = Path(tmp) / 'canary'
            (root / 'pyproject.toml').write_text('[project]\nname="demo"\nversion="1"\n[tool.github-to-hermes]\npython-entrypoint="demo:run"\n', encoding='utf-8')
            (root / 'demo.py').write_text('from pathlib import Path\nPath(' + repr(str(marker)) + ').touch()\ndef run():\n return 1\n', encoding='utf-8')
            detector.detect_type(str(root))
            result = fixed.generate(root, Path(tmp) / 'out', 'demo', {'type':'python_pkg'})
            self.assertTrue(Path(result['output_path']).exists())
            self.assertFalse(marker.exists())

    def test_mcp_config_limit_and_invalid_toml_privacy(self):
        mcp = load('metadata_mcp', 'generators/deferred_mcp.py')
        fixed = load('metadata_fixed2', 'generators/fixed_adapter.py')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'src'; root.mkdir(); out = Path(tmp) / 'out'
            (root / 'mcp.json').write_text('secret-canary' * 20)
            with patch.object(mcp, 'METADATA_LIMIT', 10):
                with self.assertRaisesRegex(ValueError, 'too large'): mcp.generate(root, out, 'demo', {})
            (root / 'pyproject.toml').write_bytes(b'\xffsecret-canary')
            with self.assertRaises(ValueError) as error: fixed.generate(root, out, 'demo', {'type':'python_pkg'})
            self.assertNotIn('secret-canary', str(error.exception))
            self.assertFalse(out.exists())
