import json
import os
import tempfile
import unittest
from pathlib import Path

from generators import fixed_adapter


class FixedMCPTests(unittest.TestCase):
    def test_host_loader_stdio_and_tamper(self):
        import subprocess
        import sys

        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); src = root / 'src'; src.mkdir()
            marker = root / 'ran'
            (src / 'pyproject.toml').write_text('[project]\nname="demo"\nversion="1"\n[project.scripts]\ndemo="demo:main"\n')
            (src / 'demo.py').write_text('from pathlib import Path\ndef main():\n Path(' + repr(str(marker)) + ').write_text("ran")\n print("fixture")\n')
            out = Path(fixed_adapter.generate(src, root / 'out', 'demo', {'type':'cli'})['output_path'])
            code = '''import asyncio,json,sys
from pathlib import Path
from hermes_cli.agent_plugins import load_agent_plugin
from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters
out, root, marker = map(Path, sys.argv[1:])
package = load_agent_plugin(out, root / "data")
assert not package.diagnostics, package.diagnostics
server = next(iter(package.mcp_servers.values()))
assert server["trust"] == "untrusted"
assert not marker.exists()
async def run():
    params = StdioServerParameters(command=sys.executable, args=server["args"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            assert len(tools) == 1 and tools[0].annotations.read_only_hint is False
            assert not marker.exists()
            result = await session.call_tool(tools[0].name, {})
            assert not result.is_error, result
            assert "fixture" in json.loads(result.content[0].text)["output"]
            assert marker.exists()
            marker.unlink()
            (out / "source.py").write_text("raise RuntimeError('tampered')")
            result = await session.call_tool(tools[0].name, {})
            assert result.is_error and "digest mismatch" in result.content[0].text
            assert not marker.exists()
asyncio.run(run())
'''
            env = dict(os.environ, HERMES_HOME=str(root / 'home'), PYTHONDONTWRITEBYTECODE='1')
            env.pop('PYTHONPATH', None)
            proc = subprocess.run([sys.executable, '-c', code, str(out), str(root), str(marker)], cwd=str(Path(sys.prefix).parent), env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_generated_package_declares_untrusted_portable_server(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp)
            (root / 'pyproject.toml').write_text('[project]\nname="demo"\nversion="1"\n[project.scripts]\ndemo="demo:main"\n')
            (root / 'demo.py').write_text('def main(): return 0\n')
            out = Path(fixed_adapter.generate(root, root / 'out', 'demo', {'type':'cli'})['output_path'])
            plugin = json.loads((out / 'plugin.json').read_text())
            config = json.loads((out / 'mcp.json').read_text())
            server, settings = next(iter(config['mcpServers'].items()))
            self.assertEqual(plugin['extensions']['com.nousresearch.hermes']['servers'][server]['trust'], 'untrusted')
            self.assertEqual(settings['command'], 'python')
            self.assertTrue(settings['args'][0].startswith('${PLUGIN_ROOT}/'))
