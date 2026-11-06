import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import ast
import base64
import io
import hashlib
import json
import subprocess
import sys

from generators import package_mcp


class PackageSecurityTests(unittest.TestCase):
    def test_runtime_collision_fails_before_source_execution(self):
        worker = Path('generators/deferred_worker.py.txt').read_text()
        tree = ast.parse(worker)
        bootstrap = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'PACKAGE_BOOTSTRAP' for t in n.targets))
        def run(top):
            files = {top + '/__init__.py': '', top + '/server.py': 'print(42)'}
            payload = {'module': top + '.server', 'files': {k: base64.b64encode(v.encode()).decode() for k, v in files.items()}}
            digests = {k: hashlib.sha256(v.encode()).hexdigest() for k, v in files.items()}
            frame = {'jsonrpc':'2.0', 'method':'notifications/reviewed-source', 'params':{'package':payload, 'digests':digests}}
            return subprocess.run([sys.executable, '-I', '-c', bootstrap], input=json.dumps(frame)+chr(10), capture_output=True, text=True)
        self.assertNotEqual(run('json').returncode, 0)
        self.assertEqual(run('fixture_package').stdout.strip(), '42')

    @unittest.skipUnless(os.name == 'nt', 'Windows runtime adapter')
    def test_single_source_read_is_bounded_before_worker(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp)
            consent = root / 'consent'
            consent.write_bytes(b'I TRUST THIS SOURCE WITH PERSISTENT FULL OS AUTHORITY')
            (root / 'source.py').write_bytes(b'placeholder')
            sizes = []
            class Oversized(io.BytesIO):
                def read(self, n=-1):
                    sizes.append(n)
                    return b'x' * 12001
            original = Path.open
            def opening(path, *args, **kwargs):
                return Oversized() if path.name == 'source.py' else original(path, *args, **kwargs)
            worker = mock.Mock()
            ns = {'__file__': str(root / 'adapter.py'), 'os': os, 'DIGEST': '', 'PACKAGE_MODULE': None, 'PersistentWorker': worker}
            exec(Path('generators/deferred_adapter.py.txt').read_text(), ns)
            with mock.patch.object(sys, 'argv', ['adapter', str(consent)]), mock.patch.object(Path, 'open', opening):
                result = ns['SessionManager']().handle({'toolName': 'x', 'arguments': {}})
            self.assertFalse(result['success'])
            self.assertEqual(sizes, [12001])
            worker.assert_not_called()

    def collect(self, name='example', server='VALUE = 1', extra=None):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp)
            pkg = root / name
            pkg.mkdir()
            (pkg / '__init__.py').write_text('')
            (pkg / 'server.py').write_text(server)
            if extra:
                (pkg / extra).write_text('VALUE = 1')
            return package_mcp.collect(root, name + '.server', lambda p, n: p.read_bytes())

    def test_stdlib_collision_rejected_and_normal_accepted(self):
        with self.assertRaisesRegex(ValueError, 'conflict'):
            self.collect('json')
        self.assertTrue(self.collect())

    def test_missing_relative_import_rejected(self):
        with self.assertRaisesRegex(ValueError, 'import'):
            self.collect(server='from . import absent')
        self.assertTrue(self.collect(server='from . import helper', extra='helper.py'))

    def test_package_module_collision_rejected_in_both_scan_orders(self):
        for reverse in (False, True):
            with self.subTest(reverse=reverse), tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
                root = Path(tmp)
                pkg = root / 'example'
                pkg.mkdir()
                (pkg / '__init__.py').write_text('')
                (pkg / 'server.py').write_text('from . import dupe')
                def file_entry():
                    (pkg / 'dupe.py').write_text('VALUE = 1')
                def package_entry():
                    (pkg / 'dupe').mkdir()
                    (pkg / 'dupe' / '__init__.py').write_text("eval('1')")
                for create in ((package_entry, file_entry) if reverse else (file_entry, package_entry)):
                    create()
                original = os.scandir
                class Ordered:
                    def __init__(self, path):
                        with original(path) as entries:
                            self.entries = sorted(entries, key=lambda e: e.name, reverse=reverse)
                    def __enter__(self): return iter(self.entries)
                    def __exit__(self, *args): pass
                with mock.patch.object(package_mcp.os, 'scandir', side_effect=Ordered):
                    with self.assertRaisesRegex(ValueError, 'collision'):
                        package_mcp.collect(root, 'example.server', lambda p, n: p.read_bytes())
                from generators import mcp_safe
                (root / 'mcp.json').write_text(json.dumps({'mcpServers': {
                    'example': {'command': 'python', 'args': ['-m', 'example.server']}
                }}))
                with self.assertRaisesRegex(ValueError, 'collision'):
                    mcp_safe.generate(root, root / 'out', 'example', {})
                self.assertFalse((root / 'out' / 'example').exists())

    def test_case_alias_collision_and_legitimate_nested_modules(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp)
            pkg = root / 'example'
            pkg.mkdir()
            (pkg / '__init__.py').write_text('')
            (pkg / 'server.py').write_text('from .nested import helper')
            nested = pkg / 'nested'
            nested.mkdir()
            (nested / '__init__.py').write_text('')
            (nested / 'helper.py').write_text('VALUE = 1')
            reads = []
            def read(path, limit):
                reads.append(path.relative_to(root).as_posix())
                return path.read_bytes()
            files, _ = package_mcp.collect(root, 'example.server', read)
            self.assertCountEqual(reads, files)
            self.assertEqual(len(reads), len(files))
            (pkg / 'NESTED.py').write_text('VALUE = 2')
            with self.assertRaisesRegex(ValueError, 'collision'):
                package_mcp.collect(root, 'example.server', read)

    def test_sensitive_filename_rejected(self):
        with self.assertRaisesRegex(ValueError, 'sensitive'):
            self.collect(extra='credentials.py')

    def test_directory_iteration_stops_at_limit(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp)
            pkg = root / 'example'
            pkg.mkdir()
            (pkg / '__init__.py').write_text('')
            (pkg / 'server.py').write_text('')
            original = os.scandir
            count = [0]
            class Many:
                def __enter__(self): return self
                def __exit__(self, *args): pass
                def __iter__(self):
                    for _ in range(1000):
                        count[0] += 1
                        yield type('Entry', (), {'name': '__init__.py', 'path': str(pkg / '__init__.py')})()
            with mock.patch.object(package_mcp.os, 'scandir', side_effect=lambda p: Many() if Path(p) == pkg else original(p)):
                with self.assertRaises(ValueError):
                    package_mcp.collect(root, 'example.server', lambda p, n: p.read_bytes())
            self.assertLess(count[0], 1000)


if __name__ == '__main__':
    unittest.main()
