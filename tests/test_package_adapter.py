import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import detector
import tools


class PackageAdapterTests(unittest.TestCase):
    def test_cli_package_real_loader_mcp_and_argv(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); repo = root / 'repo'; pkg = repo / 'src' / 'app'; pkg.mkdir(parents=True)
            marker = root / 'ran'
            (repo / 'pyproject.toml').write_text('[project]\nname="app"\nversion="1"\n[project.scripts]\napp="app.cli:main"\n')
            (pkg / '__init__.py').write_text('')
            (pkg / 'helper.py').write_text('def value(x): return "value=" + x\n')
            (pkg / 'cli.py').write_text('import sys\nfrom pathlib import Path\nfrom .helper import value\ndef main():\n Path(' + repr(str(marker)) + ').write_text("ran")\n print(value(sys.argv[1]))\n')
            report = detector.detect_type(str(repo))
            report['repo_path'] = str(repo)
            self.assertEqual(report['type'], 'cli')
            converted = json.loads(tools._convert_detected(report, '', 'app', str(root / 'out')))
            self.assertTrue(converted['success'], converted)
            out = Path(converted['output_path'])
            code = '''import asyncio,json,sys
from pathlib import Path
from hermes_cli.agent_plugins import load_agent_plugin
from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters
out, root, marker = map(Path, sys.argv[1:])
package=load_agent_plugin(out, root/'data')
assert not package.diagnostics, package.diagnostics
server=next(iter(package.mcp_servers.values()))
assert server['trust']=='untrusted' and not marker.exists()
async def main():
 async with stdio_client(StdioServerParameters(command=sys.executable,args=server['args'])) as (read,write):
  async with ClientSession(read,write) as session:
   await session.initialize()
   tool=(await session.list_tools()).tools[0]
   assert not marker.exists() and tool.annotations.read_only_hint is False
   assert tool.input_schema['properties']['argv']['items']['type']=='string'
   result=await session.call_tool(tool.name, {'argv':['hello']})
   assert not result.is_error, result
   assert 'value=hello' in json.loads(result.content[0].text)['output']
   assert marker.exists()
   marker.unlink()
   (out/'source/app/helper.py').write_text('raise RuntimeError("tampered")')
   result=await session.call_tool(tool.name, {'argv':['hello']})
   assert result.is_error and 'digest mismatch' in result.content[0].text
   assert not marker.exists()
asyncio.run(main())
'''
            env = dict(os.environ, HERMES_HOME=str(root / 'home'), PYTHONDONTWRITEBYTECODE='1')
            env.pop('PYTHONPATH', None)
            proc = subprocess.run([sys.executable, '-c', code, str(out), str(root), str(marker)], cwd=str(Path(sys.prefix).parent), env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_src_package_typed_inputs_and_support_file_tamper(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); src = root / 'repo'; pkg = src / 'src' / 'demo'; pkg.mkdir(parents=True)
            (src / 'pyproject.toml').write_text('[project]\nname="demo"\nversion="1"\n[tool.github-to-hermes]\npython-entrypoint="demo.api:run"\n')
            (pkg / '__init__.py').write_text('')
            (pkg / 'api.py').write_text('from .helper import greet\ndef run(name: str, count: int = 1):\n return greet(name) * count\n')
            (pkg / 'helper.py').write_text('def greet(name): return "Hi " + name\n')
            report = detector.detect_type(str(src))
            report['repo_path'] = str(src)
            self.assertEqual(report['type'], 'python_pkg')
            result = json.loads(tools._convert_detected(report, '', 'demo', str(root / 'out')))
            self.assertTrue(result['success'], result)
            out = Path(result['output_path'])
            spec = importlib.util.spec_from_file_location('package_adapter_test', out / 'adapter.py')
            adapter = importlib.util.module_from_spec(spec); spec.loader.exec_module(adapter)
            self.assertEqual(adapter.SCHEMA['required'], ['name'])
            with patch.object(adapter.subprocess, 'Popen', side_effect=AssertionError('sink reached')) as sink:
                invalid = json.loads(adapter.handle({'name': 'x', 'count': True}))
                self.assertFalse(invalid['success']); sink.assert_not_called()
            answer = json.loads(adapter.handle({'name': 'Ada', 'count': 2}))
            self.assertTrue(answer['success'], answer)
            self.assertEqual(json.loads(answer['output']), 'Hi AdaHi Ada')
            (out / 'source' / 'demo' / 'helper.py').write_text('raise RuntimeError("tampered")')
            with patch.object(adapter.subprocess, 'Popen', side_effect=AssertionError('sink reached')) as sink:
                answer = json.loads(adapter.handle({'name': 'Ada'}))
                self.assertFalse(answer['success']); self.assertIn('digest mismatch', answer['error']); sink.assert_not_called()
