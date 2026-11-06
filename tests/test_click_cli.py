from host_test_config import host_test_config
import json
import os
from pathlib import Path
import tempfile
import unittest
import subprocess
import sys

from generators import fixed_adapter


class ClickCliTests(unittest.TestCase):
    def test_real_host_click_group(self):
        host, host_python = host_test_config(windows_only=False)

        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); src = root / 'src'; src.mkdir(); marker = root / 'marker'
            (src / 'pyproject.toml').write_text('[project]\nname="demo"\nversion="1"\ndependencies=["click>=8"]\n[project.scripts]\ndemo="demo:main"\n')
            (src / 'demo.py').write_text('import click\nfrom pathlib import Path\nPath(' + repr(str(marker)) + ').touch()\n@click.group()\ndef main(): pass\n@main.command()\n@click.argument("value")\ndef echo(value):\n click.echo("notice", err=True)\n click.echo(value)\n')
            out = Path(fixed_adapter.generate(src, root / 'out', 'demo', {'type':'cli'})['output_path'])
            code = r'''
import os, sys
from pathlib import Path
from hermes_cli.agent_plugins import load_agent_plugin
from tools import mcp_tool_discovery, mcp_tool_lifecycle
from tools.terminal_tool import set_approval_callback
from model_tools import handle_function_call
out, root, marker = map(Path, sys.argv[1:])
pkg = load_agent_plugin(out, root / 'data'); assert not pkg.diagnostics, pkg.diagnostics
name, cfg = next(iter(pkg.mcp_servers.items()))
try:
    tools = mcp_tool_discovery.register_mcp_servers({name: cfg}); assert len(tools) == 1
    tool = tools[0]; assert not marker.exists()
    os.environ['HERMES_SINGLE_QUERY_SESSION'] = '1'
    assert 'not approve' in handle_function_call(tool, {'argv':['--help']}).lower()
    assert not marker.exists()
    os.environ.pop('HERMES_SINGLE_QUERY_SESSION'); os.environ['HERMES_INTERACTIVE']='1'
    set_approval_callback(lambda *a, **k: 'n')
    assert 'not approve' in handle_function_call(tool, {'argv':['--help']}).lower()
    assert not marker.exists()
    set_approval_callback(lambda *a, **k: 'once')
    result = handle_function_call(tool, {'argv':['--help']}); assert 'Commands:' in result, result
    assert marker.exists(); marker.unlink()
    result = handle_function_call(tool, {'argv':['echo','literal;not-a-shell']}); assert 'literal;not-a-shell' in result and 'notice' in result, result
    marker.unlink()
    result = handle_function_call(tool, {'argv':['x'] * 65}); assert not marker.exists(), result
    (out / 'source.py').write_text('tampered')
    result = handle_function_call(tool, {'argv':['--help']}); assert 'digest mismatch' in result.lower(), result
finally:
    set_approval_callback(None); mcp_tool_lifecycle.shutdown_mcp_servers()
'''
            env = dict(os.environ, HERMES_HOME=str(root / 'home'), TMPDIR=tmp, TMP=tmp, TEMP=tmp, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH='')
            env['PATH'] = str(host_python.parent) + os.pathsep + env.get('PATH', '')
            for key in ('HERMES_SINGLE_QUERY_SESSION','HERMES_YOLO_MODE','HERMES_GATEWAY_SESSION','HERMES_SESSION_PLATFORM','HERMES_CRON_SESSION'): env.pop(key, None)
            result = subprocess.run([str(host_python), '-c', code, str(out), str(root), str(marker)], cwd=host, env=env, capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_file_semantics_rejected(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp)
            (root / 'pyproject.toml').write_text('[project]\nname="demo"\nversion="1"\ndependencies=["click"]\n[project.scripts]\ndemo="demo:main"\n')
            (root / 'demo.py').write_text('import click\n@click.command()\ndef main(): print(__file__)\n')
            with self.assertRaisesRegex(ValueError, '__file__'):
                fixed_adapter.generate(root, root / 'out', 'demo', {'type':'cli'})
    def test_click_entrypoint_conversion(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp)
            (root / 'pyproject.toml').write_text('[project]\nname="demo"\nversion="1"\ndependencies=["click>=8"]\n[project.scripts]\ndemo="demo:main"\n')
            source = b'import click\n@click.command()\n@click.option("--name")\ndef main(name):\n click.echo(name)\n'
            (root / 'demo.py').write_bytes(source)
            out = Path(fixed_adapter.generate(root, root / 'out', 'demo', {'type': 'cli'})['output_path'])
            self.assertEqual((out / 'source.py').read_bytes(), source)
