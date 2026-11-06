import json
import os
from pathlib import Path
import tempfile
import unittest

from generators import mcp_safe


class DeferredPortabilityTests(unittest.TestCase):
    def test_invalid_request_does_not_start_worker(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); src = root / 'src'; src.mkdir()
            (src / 'server.py').write_text('import asyncio\n', encoding='utf-8')
            (src / 'mcp.json').write_text(json.dumps({'mcpServers': {'fixture': {'command': 'python', 'args': ['server.py']}}}))
            out = Path(mcp_safe.generate(src, root / 'out', 'fixture', {})['output_path'])
            import importlib.util
            spec = importlib.util.spec_from_file_location('fixture_adapter', out / 'adapter.py')
            adapter = importlib.util.module_from_spec(spec); spec.loader.exec_module(adapter)
            from unittest.mock import patch
            with patch.dict(os.environ, {'PLUGIN_DATA':str(root / 'data')}):
                manager = adapter.SessionManager()
                result = manager.handle({'toolName':'x', 'arguments':[], 'extra':1})
            self.assertFalse(result['success'])
            self.assertIsNone(manager.worker)

    def test_missing_runtime_and_sdk_fail_without_running_source(self):
        import subprocess, sys
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); src = root / 'src'; src.mkdir()
            marker = root / 'marker'
            (src / 'server.py').write_text('from pathlib import Path\nPath(' + repr(str(marker)) + ').touch()\n', encoding='utf-8')
            (src / 'mcp.json').write_text(json.dumps({'mcpServers': {'fixture': {'command': 'python', 'args': ['server.py']}}}))
            out = Path(mcp_safe.generate(src, root / 'out', 'fixture', {})['output_path'])
            cfg = next(iter(json.loads((out / 'mcp.json').read_text())['mcpServers'].values()))
            env = dict(os.environ, PATH=str(root))
            env.pop('PYTHONPATH', None)
            env.pop('PYTHONHOME', None)
            # Windows CreateProcess also searches the parent executable directory,
            # so PATH alone cannot reliably hide a colocated python.exe.
            with self.assertRaises(FileNotFoundError):
                subprocess.run(['g2h-definitely-missing-python.exe', str(out / 'server.py')], cwd=root, env=env, capture_output=True, timeout=10)
            proc = subprocess.run([sys.executable, '-S', str(out / 'server.py')], cwd=root, env=env, capture_output=True, timeout=10)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn(b"No module named 'mcp'", proc.stderr)
            self.assertFalse(marker.exists())

    def test_rejects_local_and_dynamic_dependencies_before_output(self):
        cases = ['import sibling', 'from . import sibling', 'print(__file__)', "open('resource.txt')", "__import__('sibling')", "import importlib\nimportlib.import_module('sibling')"]
        for code in cases:
            with self.subTest(code=code), tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
                root = Path(tmp); src = root / 'src'; src.mkdir()
                (src / 'server.py').write_text(code, encoding='utf-8')
                (src / 'sibling.py').write_text('value=1', encoding='utf-8')
                (src / 'mcp.json').write_text(json.dumps({'mcpServers': {'fixture': {'command': 'python', 'args': ['server.py']}}}))
                with self.assertRaises(ValueError):
                    mcp_safe.generate(src, root / 'out', 'fixture', {})
                self.assertFalse((root / 'out' / 'fixture').exists())
