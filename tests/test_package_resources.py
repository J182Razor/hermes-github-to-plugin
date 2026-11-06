from host_test_config import host_test_config
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

import detector
import tools


class PackageResourceTests(unittest.TestCase):
    def test_generator_does_not_use_unbounded_read_bytes(self):
        from generators import package_adapter
        from unittest.mock import patch
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); repo = root / 'repo'; pkg = repo / 'demo'; pkg.mkdir(parents=True)
            (repo / 'pyproject.toml').write_text('[project]\nname="demo"\nversion="1"\n[tool.github-to-hermes]\npython-entrypoint="demo.api:run"\n[tool.setuptools.package-data]\ndemo=["data.txt"]\n')
            (pkg / '__init__.py').write_text(''); (pkg / 'api.py').write_text('def run(): return "ok"\n'); (pkg / 'data.txt').write_text('safe')
            with patch.object(Path, 'read_bytes', side_effect=AssertionError('unbounded read')):
                package_adapter.generate(repo, root / 'out', 'demo', {'type':'python_pkg'})

    def test_generator_enforces_cumulative_file_and_resource_limits(self):
        from generators import package_adapter
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); repo = root / 'repo'; pkg = repo / 'demo'; pkg.mkdir(parents=True)
            meta = '[project]\nname="demo"\nversion="1"\n[tool.github-to-hermes]\npython-entrypoint="demo.api:run"\n'
            (pkg / '__init__.py').write_text(''); (pkg / 'api.py').write_text('def run(): return "ok"\n')
            for i in range(511): (pkg / ('m%d.py' % i)).write_text('')
            (repo / 'pyproject.toml').write_text(meta)
            with self.assertRaisesRegex(ValueError, 'too many package files'):
                package_adapter.generate(repo, root / 'out', 'count', {'type':'python_pkg'})
            for p in pkg.glob('m*.py'): p.unlink()
            names = ['r%d.dat' % i for i in range(5)]
            for name in names: (pkg / name).write_bytes(b'x' * 262144)
            (repo / 'pyproject.toml').write_text(meta + '[tool.setuptools.package-data]\ndemo=' + json.dumps(names) + '\n')
            with self.assertRaisesRegex(ValueError, 'too large'):
                package_adapter.generate(repo, root / 'out', 'size', {'type':'python_pkg'})
            self.assertFalse((root / 'out').exists())

    def test_traversable_exposes_tracked_python_and_subpackages(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); repo = root / 'repo'; pkg = repo / 'demo'; sub = pkg / 'sub'; sub.mkdir(parents=True)
            (repo / 'pyproject.toml').write_text('[project]\nname="demo"\nversion="1"\n[tool.github-to-hermes]\npython-entrypoint="demo.api:run"\n[tool.setuptools.package-data]\ndemo=["message.txt"]\n')
            (pkg / '__init__.py').write_bytes(b'# init\n'); (sub / '__init__.py').write_bytes(b'# sub\n')
            (pkg / 'message.txt').write_bytes(b'\x00\xff')
            (pkg / 'api.py').write_text('from importlib.resources import files\nimport sys\ndef run():\n p=files(__package__); s=p.joinpath("sub"); data=sys.modules[__name__].__loader__.exec_module.__globals__["files"]\n try: data["demo/message.txt"]="evil"\n except TypeError: immutable=True\n else: immutable=False\n return [s.is_dir(), sorted(x.name for x in p.iterdir()), s.joinpath("__init__.py").read_text(), list(p.joinpath("message.txt").read_bytes()), p.joinpath("missing").is_file(), immutable]\n')
            report = detector.detect_type(str(repo)); report['repo_path'] = str(repo)
            result = json.loads(tools._convert_detected(report, '', 'demo', str(root / 'out')))
            self.assertTrue(result['success'], result)
            spec = importlib.util.spec_from_file_location('resource_adapter', Path(result['output_path']) / 'adapter.py')
            adapter = importlib.util.module_from_spec(spec); spec.loader.exec_module(adapter)
            result = json.loads(adapter.handle({})); self.assertTrue(result['success'], result)
            self.assertEqual(json.loads(result['output']), [True, ['__init__.py', 'api.py', 'message.txt', 'sub'], '# sub\n', [0, 255], False, True])

    def test_declared_resource_is_read_from_verified_bytes(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); repo = root / 'repo'; pkg = repo / 'demo'; pkg.mkdir(parents=True)
            (repo / 'pyproject.toml').write_text('[project]\nname="demo"\nversion="1"\n[tool.github-to-hermes]\npython-entrypoint="demo.api:run"\n[tool.setuptools.package-data]\ndemo=["message.txt"]\n')
            (pkg / '__init__.py').write_text('')
            (pkg / 'api.py').write_text('from importlib.resources import files\ndef run():\n return files(__package__).joinpath("message.txt").read_text(encoding="utf-8")\n')
            (pkg / 'message.txt').write_bytes('héllo'.encode())
            report = detector.detect_type(str(repo)); report['repo_path'] = str(repo)
            result = json.loads(tools._convert_detected(report, '', 'demo', str(root / 'out')))
            self.assertTrue(result['success'], result)
            out = Path(result['output_path'])
            spec = importlib.util.spec_from_file_location('resource_adapter', out / 'adapter.py')
            adapter = importlib.util.module_from_spec(spec); spec.loader.exec_module(adapter)
            from unittest.mock import patch
            with patch.object(Path, 'read_bytes', side_effect=AssertionError('unbounded read')):
                result = json.loads(adapter.handle({}))
            self.assertTrue(result['success'], result)
            self.assertEqual(json.loads(result['output']), 'héllo')
            (out / 'source/demo/message.txt').write_text('tampered')
            result = json.loads(adapter.handle({}))
            self.assertFalse(result['success']); self.assertIn('digest mismatch', result['error'])

    def test_invalid_declarations_and_sensitive_resources_fail_closed(self):
        from generators import package_adapter
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); repo = root / 'repo'; pkg = repo / 'demo'; pkg.mkdir(parents=True)
            (pkg / '__init__.py').write_text('')
            (pkg / 'api.py').write_text('def run(): return "ok"\n')
            base = '[project]\nname="demo"\nversion="1"\n[tool.github-to-hermes]\npython-entrypoint="demo.api:run"\n'
            for index, (decl, filename, content) in enumerate([
                ('../escape.txt', None, b''), ('*.txt', None, b''), ('.env', '.env', b'fake'),
                ('token.txt', 'token.txt', b'fake'), ('data.txt', 'data.txt', b'-----BEGIN PRIVATE KEY-----'),
                ('missing.txt', None, b''), ('other.txt', 'undeclared.txt', b'safe')]):
                with self.subTest(decl=decl):
                    (repo / 'pyproject.toml').write_text(base + '[tool.setuptools.package-data]\ndemo=[' + json.dumps(decl) + ']\n')
                    if filename: (pkg / filename).write_bytes(content)
                    with self.assertRaises(ValueError): package_adapter.generate(repo, root / 'out', 'bad' + str(index), {'type':'python_pkg'})
                    self.assertFalse((root / 'out' / ('bad' + str(index))).exists())
                    if filename: (pkg / filename).unlink()

    def test_resource_symlink_rejected(self):
        from generators import package_adapter
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); repo = root / 'repo'; pkg = repo / 'demo'; pkg.mkdir(parents=True)
            (repo / 'pyproject.toml').write_text('[project]\nname="demo"\nversion="1"\n[tool.github-to-hermes]\npython-entrypoint="demo.api:run"\n[tool.setuptools.package-data]\ndemo=["data.txt"]\n')
            (pkg / '__init__.py').write_text(''); (pkg / 'api.py').write_text('def run(): return "ok"\n')
            target = root / 'target.txt'; target.write_text('safe')
            try:
                (pkg / 'data.txt').symlink_to(target)
            except OSError as exc:
                self.skipTest('symlink unavailable: ' + str(exc))
            with self.assertRaisesRegex(ValueError, 'symlink|reparse'):
                package_adapter.generate(repo, root / 'out', 'demo', {'type':'python_pkg'})
            self.assertFalse((root / 'out' / 'demo').exists())

    def test_resource_real_hermes_dispatch_and_approval(self):
        host, host_python = host_test_config(windows_only=False)
        import subprocess
        import sys

        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); repo = root / 'repo'; pkg = repo / 'demo'; pkg.mkdir(parents=True); marker = root / 'marker'
            (repo / 'pyproject.toml').write_text('[project]\nname="demo"\nversion="1"\n[tool.github-to-hermes]\npython-entrypoint="demo.api:run"\n[tool.setuptools.package-data]\ndemo=["message.txt"]\n')
            (pkg / '__init__.py').write_text('')
            (pkg / 'api.py').write_text('from importlib.resources import files\nfrom pathlib import Path\nPath(' + repr(str(marker)) + ').write_text("ran")\ndef run():\n return files(__package__).joinpath("message.txt").read_text(encoding="utf-8")\n')
            (pkg / 'message.txt').write_bytes('héllo'.encode())
            report = detector.detect_type(str(repo)); report['repo_path'] = str(repo)
            result = json.loads(tools._convert_detected(report, '', 'demo', str(root / 'out')))
            self.assertTrue(result['success'], result); self.assertFalse(marker.exists())
            code = r'''import json, os, sys
from pathlib import Path
from hermes_cli.agent_plugins import load_agent_plugin
from tools import mcp_tool_discovery, mcp_tool_lifecycle
from tools.terminal_tool import set_approval_callback
from model_tools import handle_function_call
out, root, marker = map(Path, sys.argv[1:])
pkg = load_agent_plugin(out, root/'data'); assert not pkg.diagnostics, pkg.diagnostics
name, config = next(iter(pkg.mcp_servers.items())); assert config['trust']=='untrusted'
try:
 names=mcp_tool_discovery.register_mcp_servers({name:config}); assert len(names)==1
 tool=names[0]; assert not marker.exists()
 os.environ['HERMES_SINGLE_QUERY_SESSION']='1'
 assert 'not approve' in handle_function_call(tool, {}).lower(); assert not marker.exists()
 os.environ.pop('HERMES_SINGLE_QUERY_SESSION'); os.environ['HERMES_INTERACTIVE']='1'
 set_approval_callback(lambda *a, **k: 'n')
 assert 'not approve' in handle_function_call(tool, {}).lower(); assert not marker.exists()
 set_approval_callback(lambda *a, **k: 'once')
 result=handle_function_call(tool, {})
 assert marker.read_text()=='ran', result
 assert json.loads(json.loads(json.loads(result)['result'])['output'])=='héllo', result
 marker.unlink(); (out/'source/demo/message.txt').write_bytes(b'tampered')
 result=handle_function_call(tool, {})
 assert 'digest mismatch' in result.lower() and not marker.exists(), result
 print('RESOURCE_HOST_OK')
finally:
 set_approval_callback(None); mcp_tool_lifecycle.shutdown_mcp_servers()
'''
            env = dict(os.environ, HERMES_HOME=str(root/'home'), TMPDIR=tmp, TMP=tmp, TEMP=tmp, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH='')
            env['PATH'] = str(host_python.parent) + os.pathsep + env.get('PATH', '')
            for key in ('HERMES_SINGLE_QUERY_SESSION','HERMES_GATEWAY_SESSION','HERMES_SESSION_PLATFORM','HERMES_CRON_SESSION','HERMES_YOLO_MODE'): env.pop(key, None)
            proc = subprocess.run([str(host_python), '-c', code, result['output_path'], str(root), str(marker)], cwd=str(host), env=env, capture_output=True, text=True, timeout=90)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn('RESOURCE_HOST_OK', proc.stdout)
