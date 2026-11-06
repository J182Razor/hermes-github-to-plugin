"""End-to-end native Hermes MCP trust gate; no external servers or credentials."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from generators import fixed_adapter

from host_test_config import host_test_config


class RealHostDispatch(unittest.TestCase):
    def setUp(self):
        self.host, self.python = host_test_config(windows_only=False)

    def _exercise_real_host(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp)
            src = root / 'src'; src.mkdir()
            marker = root / 'source-ran'
            env = dict(os.environ, HERMES_HOME=str(root / 'home'), TMPDIR=tmp, TMP=tmp, TEMP=tmp, PYTHONDONTWRITEBYTECODE='1')
            env['PATH'] = str(self.python.parent) + os.pathsep + env.get('PATH', '')
            for key in ('PYTHONHOME', 'HERMES_SINGLE_QUERY_SESSION', 'HERMES_GATEWAY_SESSION', 'HERMES_SESSION_PLATFORM', 'HERMES_CRON_SESSION', 'HERMES_YOLO_MODE'):
                env.pop(key, None)
            env['PYTHONPATH'] = ''
            # Probe through an unmodified generated artifact and the real host;
            # its isolated worker, not this test process, owns dependency checks.
            probe = root / 'probe'; probe.mkdir()
            probe_marker = root / 'probe-result'
            (probe / 'pyproject.toml').write_text('[project]\nname="probe"\nversion="1"\n[project.scripts]\nprobe="probe:main"\n')
            (probe / 'probe.py').write_text('import json, sys\nfrom pathlib import Path\nfrom importlib.metadata import version\ndef main():\n Path(' + repr(str(probe_marker)) + ').write_text(json.dumps([sys.executable, version("packaging")]))\n')
            probe_out = Path(fixed_adapter.generate(probe, root / 'out', 'probe', {'type': 'cli'})['output_path'])
            probe_code = r'''
import os, sys
from pathlib import Path
from hermes_cli.agent_plugins import load_agent_plugin
from tools import mcp_tool_discovery, mcp_tool_lifecycle
from tools.mcp_tool_transport import _connect_inputs
from model_tools import handle_function_call
out, root = map(Path, sys.argv[1:])
pkg = load_agent_plugin(out, root / 'probe-data')
assert not pkg.diagnostics, pkg.diagnostics
name, config = next(iter(pkg.mcp_servers.items()))
assert config['command'] == 'python'
print('RESOLVED_COMMAND', _connect_inputs(name, config)[0][0], flush=True)
config['trust'] = 'full'
try:
    names = mcp_tool_discovery.register_mcp_servers({name: config})
    assert len(names) == 1, names
    result = handle_function_call(names[0], {})
    assert (root / 'probe-result').exists(), result
finally:
    mcp_tool_lifecycle.shutdown_mcp_servers()
'''
            probe_proc = subprocess.run([str(self.python), '-c', probe_code, str(probe_out), str(root)], cwd=str(self.host), env=env, capture_output=True, text=True, timeout=90)
            self.assertEqual(probe_proc.returncode, 0, probe_proc.stdout + probe_proc.stderr)
            resolved_lines = [line for line in probe_proc.stdout.splitlines() if line.startswith('RESOLVED_COMMAND ')]
            self.assertEqual(len(resolved_lines), 1)
            import json
            worker_executable, installed = json.loads(probe_marker.read_text())
            from importlib.metadata import version
            (src / 'pyproject.toml').write_text('[project]\nname="demo"\nversion="1"\ndependencies=["packaging==' + installed + '"]\n[project.scripts]\ndemo="demo:main"\n')
            (src / 'demo.py').write_text('import json, sys\nfrom pathlib import Path\nfrom importlib.metadata import version\nPath(' + repr(str(marker)) + ').write_text(json.dumps([sys.executable, version("packaging")]))\ndef main():\n print("fixture")\n')
            out = Path(fixed_adapter.generate(src, root / 'out', 'demo', {'type': 'cli'})['output_path'])
            for name, requirement in [('missing', 'g2h-nonexistent-distribution-xyz>=1'), ('mismatch', 'packaging==999999999')]:
                (src / 'pyproject.toml').write_text('[project]\nname="demo"\nversion="1"\ndependencies=' + __import__('json').dumps([requirement]) + '\n[project.scripts]\ndemo="demo:main"\n')
                fixed_adapter.generate(src, root / 'out', name, {'type': 'cli'})
            code = r'''
import json, os, sys
from pathlib import Path
from hermes_cli.agent_plugins import load_agent_plugin
from tools import mcp_tool, mcp_tool_discovery, mcp_tool_lifecycle
from tools.terminal_tool import set_approval_callback
from model_tools import handle_function_call
out, root, marker = map(Path, sys.argv[1:4])
expected_worker = json.loads(sys.argv[4])
package = load_agent_plugin(out, root / 'data')
assert not package.diagnostics, package.diagnostics
name, config = next(iter(package.mcp_servers.items()))
assert config['trust'] == 'untrusted'
# The portable command resolves through the isolated test PATH.
assert config['command'] == 'python'
try:
    names = mcp_tool_discovery.register_mcp_servers({name: config})
    assert len(names) == 1, names
    tool = names[0]
    hints = list(mcp_tool._tool_read_only_hints.values())[0]
    assert list(hints.values()) == [False], hints
    assert not marker.exists()
    os.environ['HERMES_SINGLE_QUERY_SESSION'] = '1'
    result = handle_function_call(tool, {})
    assert 'not approve' in result.lower(), result
    assert not marker.exists()
    os.environ.pop('HERMES_SINGLE_QUERY_SESSION')
    os.environ['HERMES_INTERACTIVE'] = '1'
    seen = []
    def answer(command, description, **kwargs):
        seen.append(command)
        return 'n'
    set_approval_callback(answer)
    result = handle_function_call(tool, {})
    assert 'not approve' in result.lower(), result
    assert len(seen) == 1 and not marker.exists(), (seen, result)
    def yes(command, description, **kwargs):
        seen.append(command)
        return 'once'
    set_approval_callback(yes)
    result = handle_function_call(tool, {})
    assert marker.exists(), result
    assert json.loads(marker.read_text()) == expected_worker, 'worker interpreter or distribution changed after probe'
    assert 'fixture' in result, result
    marker.unlink()
    with (out / 'source.py').open('wb') as source:
        source.truncate(8 * 1024 * 1024)
    result = handle_function_call(tool, {})
    assert 'source file too large' in result.lower(), result
    assert not marker.exists()
    (out / 'source.py').write_text("raise RuntimeError('tampered')")
    result = handle_function_call(tool, {})
    assert 'digest mismatch' in result.lower(), result
    assert not marker.exists()
    print('HOST_DISPATCH_OK: no-human denied; explicit denial; approval ran; tamper denied')
finally:
    set_approval_callback(None)
    mcp_tool_lifecycle.shutdown_mcp_servers()
# A user can explicitly override the plugin's declared trust in host policy.
# Re-register the same server with that user-owned effective policy: this is
# deliberately NOT represented as a mandatory human gate.
(out / 'source.py').write_text((root / 'src' / 'demo.py').read_text())
config['trust'] = 'full'
os.environ['HERMES_SINGLE_QUERY_SESSION'] = '1'
try:
    names = mcp_tool_discovery.register_mcp_servers({name: config})
    assert len(names) == 1, names
    result = handle_function_call(names[0], {})
    assert marker.exists() and 'fixture' in result, result
    print('POLICY_OVERRIDE_OK: full trust bypasses call approval')
finally:
    mcp_tool_lifecycle.shutdown_mcp_servers()
# Fresh servers use the original manifest executable and simulated approval.
for variant, expected in [('missing', 'missing required distribution'), ('mismatch', 'dependency version mismatch')]:
    marker.unlink(missing_ok=True)
    pkg = load_agent_plugin(out.parent / variant, root / ('data-' + variant))
    assert not pkg.diagnostics, pkg.diagnostics
    n, cfg = next(iter(pkg.mcp_servers.items()))
    try:
        names = mcp_tool_discovery.register_mcp_servers({n: cfg})
        os.environ.pop('HERMES_SINGLE_QUERY_SESSION', None)
        os.environ['HERMES_INTERACTIVE'] = '1'
        set_approval_callback(lambda *a, **k: 'once')
        result = handle_function_call(names[0], {})
        assert expected in result.lower() and not marker.exists(), result
    finally:
        set_approval_callback(None)
        mcp_tool_lifecycle.shutdown_mcp_servers()
print('DEPENDENCY_HOST_FAILURES_OK')
'''
            proc = subprocess.run([str(self.python), '-c', code, str(out), str(root), str(marker), json.dumps([worker_executable, installed])], cwd=str(self.host), env=env, capture_output=True, text=True, timeout=90)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn('HOST_DISPATCH_OK', proc.stdout)
            self.assertIn('DEPENDENCY_HOST_FAILURES_OK', proc.stdout)
            return (sys.executable, version('packaging')), (worker_executable, installed)

    def test_real_host_denial_approval_and_tamper(self):
        self._exercise_real_host()

    def test_divergent_parent_and_worker_use_worker_dependencies(self):
        parent, worker = self._exercise_real_host()
        if parent[1] == worker[1]:
            self.skipTest('parent and worker packaging versions do not diverge on this host')
        self.assertNotEqual(parent[1], worker[1])
