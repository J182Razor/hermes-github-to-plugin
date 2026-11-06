from host_test_config import host_test_config
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import detector
import tools
from generators import fixed_adapter


class SingleTypedHostTests(unittest.TestCase):
    def test_unsupported_typed_signatures_and_rebindings_fail_before_output(self):
        cases = [
            'def run(x: str, *args): return x\n',
            'def run(x: list): return x\n',
            'def run(x: int = "wrong"): return x\n',
            'def run(x: float = 1e999): return x\n',
            'def run(x: str): return x\nrun = print\n',
            '@staticmethod\ndef run(x: str): return x\n',
            'def run(x: str): return eval(x)\n',
            'import importlib\ndef run(x: str): return x\n',
            'from .helper import value\ndef run(x: str): return x\n',
            'import helper\ndef run(x: str): return x\n',
        ]
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); repo = root / 'repo'; repo.mkdir()
            (repo / 'helper.py').write_text('value = 1\n')
            (repo / 'pyproject.toml').write_text('[project]\nname="demo"\nversion="1"\n[tool.github-to-hermes]\npython-entrypoint="demo:run"\n')
            for index, text in enumerate(cases):
                with self.subTest(text=text):
                    (repo / 'demo.py').write_text(text)
                    with self.assertRaises(ValueError):
                        fixed_adapter.generate(repo, root / 'out', 'demo' + str(index), {'type':'python_pkg'})
                    self.assertFalse((root / 'out' / ('demo' + str(index))).exists())

    def test_collision_logs_and_finite_numbers(self):
        import importlib.util
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); repo = root / 'repo'; repo.mkdir()
            (repo / 'pyproject.toml').write_text('[project]\nname="demo"\nversion="1"\n[tool.github-to-hermes]\npython-entrypoint="json:run"\n')
            (repo / 'json.py').write_text('def run(x: float):\n print("log")\n return x\n')
            result = fixed_adapter.generate(repo, root / 'out', 'demo', {'type': 'python_pkg'})
            spec = importlib.util.spec_from_file_location('test_adapter', Path(result['output_path']) / 'adapter.py')
            adapter = importlib.util.module_from_spec(spec); spec.loader.exec_module(adapter)
            result = json.loads(adapter.handle({'x': 1.5}))
            self.assertTrue(result['success'], result)
            self.assertEqual(json.loads(result['output']), 1.5)
            self.assertEqual(result['stdout'], 'log\n')
            for value in (float('nan'), float('inf'), -float('inf')):
                self.assertFalse(json.loads(adapter.handle({'x': value}))['success'])

    def test_unicode_stdout_stderr_and_raw_bytes(self):
        import importlib.util
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); repo = root / 'repo'; repo.mkdir()
            (repo / 'pyproject.toml').write_text('[project]\nname="demo"\nversion="1"\n[tool.github-to-hermes]\npython-entrypoint="demo:run"\n')
            (repo / 'demo.py').write_text('import os, sys\ndef run(x: str):\n print(x)\n print(x, file=sys.stderr)\n os.write(2, b"raw-\\xff\\n")\n return x\n', encoding='utf-8')
            generated = fixed_adapter.generate(repo, root / 'out', 'demo', {'type':'python_pkg'})
            spec = importlib.util.spec_from_file_location('unicode_adapter', Path(generated['output_path']) / 'adapter.py')
            adapter = importlib.util.module_from_spec(spec); spec.loader.exec_module(adapter)
            result = json.loads(adapter.handle({'x': '\u96ea'}))
            self.assertTrue(result['success'], result)
            self.assertEqual(json.loads(result['output']), '\u96ea')
            self.assertIn('\u96ea', result['stdout'])
            self.assertIn('\u96ea', result['stderr'])
            self.assertIn('raw-\ufffd', result['stderr'])
            failed_source = 'import sys\ndef run(x: str):\n print(x)\n print(x, file=sys.stderr)\n raise RuntimeError("failure")\n'
            (repo / 'demo.py').write_text(failed_source, encoding='utf-8')
            generated = fixed_adapter.generate(repo, root / 'out', 'failed', {'type':'python_pkg'})
            spec = importlib.util.spec_from_file_location('failed_adapter', Path(generated['output_path']) / 'adapter.py')
            adapter = importlib.util.module_from_spec(spec); spec.loader.exec_module(adapter)
            failed = json.loads(adapter.handle({'x': '\u96ea'}))
            self.assertFalse(failed['success'])
            self.assertIn('\u96ea', failed['stdout'])
            self.assertIn('\u96ea', failed['stderr'])
            self.assertIn('failure', failed['error'])

    def test_non_windows_memory_request_fails_before_spawn(self):
        import importlib.util
        from unittest.mock import patch
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); repo = root / 'repo'; repo.mkdir()
            (repo / 'pyproject.toml').write_text('[project]\nname="demo"\nversion="1"\n[tool.github-to-hermes]\npython-entrypoint="demo:run"\n')
            (repo / 'demo.py').write_text('def run(x: int): return x\n')
            generated = fixed_adapter.generate(repo, root / 'out', 'demo', {'type':'python_pkg'})
            spec = importlib.util.spec_from_file_location('memory_adapter', Path(generated['output_path']) / 'adapter.py')
            adapter = importlib.util.module_from_spec(spec); spec.loader.exec_module(adapter)
            with patch.object(adapter.os, 'name', 'posix'), patch.object(adapter.subprocess, 'Popen') as spawn:
                with self.assertRaisesRegex(ValueError, 'memory limit'):
                    adapter._run_contained(['unused'], b'', memory_limit=True)
                spawn.assert_not_called()

    def test_native_stdout_is_log_not_protocol(self):
        import importlib.util
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); repo = root / 'repo'; repo.mkdir()
            (repo / 'pyproject.toml').write_text('[project]\nname="demo"\nversion="1"\n[tool.github-to-hermes]\npython-entrypoint="demo:run"\n')
            (repo / 'demo.py').write_text('import os, atexit\ndef run(x: int):\n os.write(1, b"native log\\n")\n atexit.register(os.write, 1, b"after result\\n")\n return x\n')
            generated = fixed_adapter.generate(repo, root / 'out', 'demo', {'type':'python_pkg'})
            spec = importlib.util.spec_from_file_location('native_adapter', Path(generated['output_path']) / 'adapter.py')
            adapter = importlib.util.module_from_spec(spec); spec.loader.exec_module(adapter)
            result = json.loads(adapter.handle({'x': 42}))
            self.assertTrue(result['success'], result)
            self.assertEqual(json.loads(result['output']), 42)
            self.assertIn('native log', result['stdout'])
            self.assertIn('after result', result['stdout'])

    def test_invalid_worker_frames_rejected(self):
        import importlib.util
        from unittest.mock import patch
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); repo = root / 'repo'; repo.mkdir()
            (repo / 'pyproject.toml').write_text('[project]\nname="demo"\nversion="1"\n[tool.github-to-hermes]\npython-entrypoint="demo:run"\n')
            (repo / 'demo.py').write_text('def run(x: int): return x\n')
            generated = fixed_adapter.generate(repo, root / 'out', 'demo', {'type':'python_pkg'})
            spec = importlib.util.spec_from_file_location('frame_adapter', Path(generated['output_path']) / 'adapter.py')
            adapter = importlib.util.module_from_spec(spec); spec.loader.exec_module(adapter)
            valid = b'\x00\x00\x00\x0c{"result":1}'
            for frame in (b'', b'bad', b'\x00\x00\x00\x02{}x', valid + valid):
                with self.subTest(frame=frame), patch.object(adapter, '_run_contained', return_value=(0, b'', b'', frame)):
                    self.assertFalse(json.loads(adapter.handle({'x': 1}))['success'])

    def test_response_pipe_limits_timeout_and_handle_isolation(self):
        import importlib.util
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); repo = root / 'repo'; repo.mkdir()
            (repo / 'pyproject.toml').write_text('[project]\nname="demo"\nversion="1"\n[tool.github-to-hermes]\npython-entrypoint="demo:run"\n')
            (repo / 'demo.py').write_text('def run(x: int): return x\n')
            generated = fixed_adapter.generate(repo, root / 'out', 'demo', {'type':'python_pkg'})
            spec = importlib.util.spec_from_file_location('limit_adapter', Path(generated['output_path']) / 'adapter.py')
            adapter = importlib.util.module_from_spec(spec); spec.loader.exec_module(adapter)
            bootstrap = 'import os,sys\nif os.name=="nt":\n import msvcrt\n fd=msvcrt.open_osfhandle(int(sys.argv[1]), os.O_WRONLY|os.O_BINARY)\nelse: fd=int(sys.argv[1])\n'
            code, stdout, stderr, frame = adapter._run_contained([sys.executable, '-c', bootstrap + 'os.write(1,b"log"); os.write(fd,b"response")'], b'', response_pipe=True)
            self.assertEqual((code, bytes(stdout), bytes(frame)), (0, b'log', b'response'))
            with self.assertRaisesRegex(ValueError, 'output limit'):
                adapter._run_contained([sys.executable, '-c', bootstrap + 'os.write(fd,b"x"*1048581)'], b'', response_pipe=True)
            with self.assertRaisesRegex(ValueError, 'timeout'):
                adapter._run_contained([sys.executable, '-c', bootstrap + 'import time; time.sleep(5)'], b'', response_pipe=True, timeout=.2)

    def test_worker_does_not_inherit_fake_secret(self):
        import importlib.util
        from unittest.mock import patch
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); repo = root / 'repo'; repo.mkdir()
            (repo / 'pyproject.toml').write_text('[project]\nname="demo"\nversion="1"\n[tool.github-to-hermes]\npython-entrypoint="demo:run"\n')
            (repo / 'demo.py').write_text('import os\ndef run(x: int): return os.getenv("G2H_FAKE_SECRET", "absent")\n')
            result = fixed_adapter.generate(repo, root / 'out', 'demo', {'type':'python_pkg'})
            spec = importlib.util.spec_from_file_location('test_env_adapter', Path(result['output_path']) / 'adapter.py')
            adapter = importlib.util.module_from_spec(spec); spec.loader.exec_module(adapter)
            with patch.dict(os.environ, {'G2H_FAKE_SECRET':'canary-not-a-secret'}):
                result = json.loads(adapter.handle({'x':1}))
            self.assertTrue(result['success'], result)
            self.assertEqual(json.loads(result['output']), 'absent')

    @unittest.skipUnless(os.name == 'nt', 'Windows job memory limit')
    def test_worker_memory_limit_on_bounded_child_allocation(self):
        import importlib.util
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); repo = root / 'repo'; repo.mkdir()
            (repo / 'pyproject.toml').write_text('[project]\nname="demo"\nversion="1"\n[tool.github-to-hermes]\npython-entrypoint="demo:run"\n')
            (repo / 'demo.py').write_text('def run(x: int):\n try:\n  a = bytearray(x * 1024 * 1024)\n  return len(a)\n except MemoryError:\n  return "limited"\n')
            result = fixed_adapter.generate(repo, root / 'out', 'demo', {'type':'python_pkg'})
            spec = importlib.util.spec_from_file_location('test_memory_adapter', Path(result['output_path']) / 'adapter.py')
            adapter = importlib.util.module_from_spec(spec); spec.loader.exec_module(adapter)
            self.assertEqual(json.loads(json.loads(adapter.handle({'x': 1}))['output']), 1048576)
            self.assertEqual(json.loads(json.loads(adapter.handle({'x': 300}))['output']), 'limited')

    def test_typed_source_through_real_host(self):
        host, host_python = host_test_config(windows_only=False)

        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); repo = root / 'repo'; repo.mkdir(); marker = root / 'ran'
            (repo / 'pyproject.toml').write_text('[project]\nname="demo"\nversion="1"\n[tool.github-to-hermes]\npython-entrypoint="json:run"\n')
            source = ('from pathlib import Path\nimport sys, os\nPath(' + repr(str(marker)) + ').open("a").write("ran\\n")\ndef run(name: str, count: int = 2, /, *, scale: float = 1.5, loud: bool = False):\n print(name)\n print(name, file=sys.stderr)\n os.write(2, b"raw-\\xff\\n")\n return [name, count, scale, loud]\n').encode()
            (repo / 'json.py').write_bytes(source)
            report = detector.detect_type(str(repo)); report['repo_path'] = str(repo)
            self.assertEqual(report['type'], 'python_pkg')
            result = json.loads(tools._convert_detected(report, '', 'demo', str(root / 'out')))
            self.assertTrue(result['success'], result)
            out = Path(result['output_path'])
            self.assertEqual((out / 'source/json.py').read_bytes(), source)
            self.assertFalse(marker.exists())
            code = r'''
import json, os, sys
from pathlib import Path
from hermes_cli.agent_plugins import load_agent_plugin
from tools import mcp_tool_discovery, mcp_tool_lifecycle
from tools.terminal_tool import set_approval_callback
from model_tools import handle_function_call
out, root, marker = map(Path, sys.argv[1:])
pkg = load_agent_plugin(out, root/'data')
assert not pkg.diagnostics, pkg.diagnostics
name, config = next(iter(pkg.mcp_servers.items())); assert config['trust']=='untrusted'
assert config['command']=='python'
try:
 names=mcp_tool_discovery.register_mcp_servers({name:config}); assert len(names)==1, names
 tool=names[0]; assert not marker.exists()
 os.environ['HERMES_SINGLE_QUERY_SESSION']='1'
 assert 'not approve' in handle_function_call(tool, {'name':'雪'}).lower(); assert not marker.exists()
 os.environ.pop('HERMES_SINGLE_QUERY_SESSION'); os.environ['HERMES_INTERACTIVE']='1'
 set_approval_callback(lambda *a, **k: 'n')
 assert 'not approve' in handle_function_call(tool, {'name':'雪'}).lower(); assert not marker.exists()
 set_approval_callback(lambda *a, **k: 'once')
 result=handle_function_call(tool, {'name':'雪; safe', 'scale':2.5, 'loud':True})
 assert marker.read_text().splitlines() == ['ran'], result
 assert json.loads(json.loads(json.loads(result)['result'])['output']) == ['雪; safe', 2, 2.5, True], result
 logs=json.loads(json.loads(result)['result'])
 assert '雪; safe' in logs['stdout'] and '雪; safe' in logs['stderr'] and 'raw-�' in logs['stderr'], result
 marker.unlink()
 result=handle_function_call(tool, {'name':'x', 'count':True})
 assert not marker.exists() and ('invalid' in result.lower() or 'error' in result.lower()), result
 (out/'source/json.py').write_text('raise RuntimeError("tampered")')
 result=handle_function_call(tool, {'name':'x'})
 assert 'digest mismatch' in result.lower() and not marker.exists(), result
 print('TYPED_HOST_OK')
finally:
 set_approval_callback(None); mcp_tool_lifecycle.shutdown_mcp_servers()
'''
            env = dict(os.environ, HERMES_HOME=str(root/'home'), TMPDIR=tmp, TMP=tmp, TEMP=tmp, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH='')
            env['PATH'] = str(host_python.parent) + os.pathsep + env.get('PATH', '')
            for key in ('HERMES_SINGLE_QUERY_SESSION','HERMES_GATEWAY_SESSION','HERMES_SESSION_PLATFORM','HERMES_CRON_SESSION','HERMES_YOLO_MODE'): env.pop(key, None)
            proc = subprocess.run([str(host_python), '-c', code, str(out), str(root), str(marker)], cwd=str(host), env=env, capture_output=True, text=True, timeout=90)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn('TYPED_HOST_OK', proc.stdout)
