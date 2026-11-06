import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from generators import mcp_safe

from host_test_config import host_test_config

class DeferredHostTests(unittest.TestCase):
    def setUp(self):
        self.host, self.python = host_test_config(windows_only=True)

    def test_host_gate_and_real_mcp(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); src = root / 'src'; src.mkdir(); marker = root / 'marker'; delayed = root / 'delayed'
            source = '''import asyncio, subprocess, sys, json
from importlib.metadata import version
from pathlib import Path
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types
Path(%r).write_text(json.dumps({'executable': sys.executable, 'version': version('mcp')}))
async def list_tools(ctx, params):
    return types.ListToolsResult(tools=[types.Tool(name='echo', description='echo', inputSchema={'type':'object','properties':{'value':{'type':'string'}}})])
async def call_tool(ctx, params):
    if params.arguments['value'] == 'fail-fixture':
        return types.CallToolResult(content=[types.TextContent(type='text', text='synthetic source failure')], isError=True)
    subprocess.Popen([sys.executable, '-c', %r], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return types.CallToolResult(content=[types.TextContent(type='text', text=params.arguments['value'])])
async def main():
    import inspect
    if 'on_list_tools' in inspect.signature(Server).parameters:
        s=Server('fixture', on_list_tools=list_tools, on_call_tool=call_tool)
    else:
        s=Server('fixture')
        @s.list_tools()
        async def listed(): return (await list_tools(None, None)).tools
        @s.call_tool()
        async def called(name, arguments):
            from types import SimpleNamespace
            result = await call_tool(None, SimpleNamespace(name=name, arguments=arguments))
            if result.isError: raise ValueError(result.content[0].text)
            return result.content
    async with stdio_server() as (r,w): await s.run(r,w,s.create_initialization_options())
if __name__=='__main__': asyncio.run(main())
''' % (str(marker), 'import time; from pathlib import Path; time.sleep(4); Path(' + repr(str(delayed)) + ').touch()')
            (src / 'server.py').write_text(source, encoding='utf-8')
            (src / 'mcp.json').write_text(json.dumps({'mcpServers':{'fixture':{'command':'python','args':['server.py']}}}))
            out = Path(mcp_safe.generate(src, root / 'out', 'fixture', {})['output_path'])
            script = r'''
import os, sys, json, shutil, subprocess
from pathlib import Path
from hermes_cli.agent_plugins import load_agent_plugin
from tools import mcp_tool_discovery, mcp_tool_lifecycle
from tools.mcp_tool_transport import _connect_inputs
from tools.terminal_tool import set_approval_callback
from model_tools import handle_function_call
out, root, marker = map(Path, sys.argv[1:])
p = load_agent_plugin(out, root / 'data')
assert not p.diagnostics, p.diagnostics
name, config = next(iter(p.mcp_servers.items()))
assert config['trust'] == 'untrusted'
assert config['command'] == 'python', config
(command, child_env, child_cwd), _ = _connect_inputs(name, config)
resolved = shutil.which(command, path=child_env.get('PATH')) or command
assert Path(resolved).is_file(), resolved
requested = os.environ.get('G2H_TEST_WORKER_PYTHON')
expected = os.environ.get('G2H_TEST_MCP_VERSION')
assert bool(requested) == bool(expected), 'explicit runs require both executable and SDK version'
if requested:
    assert os.path.samefile(requested, resolved), (requested, resolved)
probe = subprocess.run([resolved, '-I', '-c', "import json,sys; from importlib.metadata import version; print(json.dumps({'executable':sys.executable,'version':version('mcp')}))"], env=child_env, cwd=child_cwd, text=True, capture_output=True, check=True)
identity = json.loads(probe.stdout)
assert os.path.samefile(resolved, identity['executable']), identity
if expected:
    assert identity['version'] == expected, identity
print('RESOLVED_IDENTITY', json.dumps(identity), flush=True)
args = {'toolName':'echo','arguments':{'value':'fixture-result'}}
try:
    tools = mcp_tool_discovery.register_mcp_servers({name:config})
    assert len(tools) == 1, tools
    tool=tools[0]
    assert not marker.exists()
    os.environ['HERMES_SINGLE_QUERY_SESSION']='1'
    result=handle_function_call(tool,args)
    assert 'not approve' in result.lower(), result
    assert not marker.exists()
    os.environ.pop('HERMES_SINGLE_QUERY_SESSION')
    os.environ['HERMES_INTERACTIVE']='1'
    seen=[]
    def deny(*a, **k): seen.append(1); return 'n'
    set_approval_callback(deny)
    result=handle_function_call(tool,args)
    assert 'not approve' in result.lower() and seen and not marker.exists(), result
    def yes(*a, **k): return 'once'
    set_approval_callback(yes)
    consent = root / 'data' / 'persistent-mcp-opt-in.txt'
    consent.parent.mkdir(parents=True, exist_ok=True)
    consent.write_text('I TRUST THIS SOURCE WITH PERSISTENT FULL OS AUTHORITY', encoding='utf-8')
    result=handle_function_call(tool,args)
    import json
    outer = json.loads(result)
    payload = json.loads(outer['result'])
    assert marker.exists() and payload['success'] is True, result
    observed = json.loads(marker.read_text())
    assert os.path.samefile(resolved, observed['executable']), (resolved, observed)
    assert observed['version'] == identity['version'], (identity, observed)
    print('SOURCE_IDENTITY', json.dumps(observed), flush=True)
    assert payload['result']['content'] == [{'type':'text','text':'fixture-result'}], result
    failure=handle_function_call(tool, {'toolName':'echo','arguments':{'value':'fail-fixture'}})
    failure_payload=json.loads(failure)
    assert 'error' in failure_payload and 'synthetic source failure' in failure_payload['error'], failure
    marker.unlink()
    (out/'source.py').write_text('tampered')
    result=handle_function_call(tool,args)
    assert 'digest mismatch' in result.lower() and not marker.exists(), result
    print('HOST_OK')
finally:
    set_approval_callback(None)
    mcp_tool_lifecycle.shutdown_mcp_servers()
'''
            env = dict(os.environ, HERMES_HOME=str(root / 'home'), TMPDIR=tmp, TMP=tmp, TEMP=tmp, PYTHONDONTWRITEBYTECODE='1')
            requested = os.environ.get('G2H_TEST_WORKER_PYTHON')
            expected = os.environ.get('G2H_TEST_MCP_VERSION')
            self.assertEqual(bool(requested), bool(expected), 'explicit runs require both executable and SDK version')
            if requested:
                worker_python = Path(requested)
                self.assertTrue(worker_python.is_file())
                env['PATH'] = str(worker_python.parent) + os.pathsep + env.get('PATH', '')
            for key in ('PYTHONHOME','HERMES_SINGLE_QUERY_SESSION','HERMES_GATEWAY_SESSION','HERMES_SESSION_PLATFORM','HERMES_CRON_SESSION','HERMES_YOLO_MODE'):
                env.pop(key, None)
            env['PYTHONPATH'] = ''
            proc = subprocess.run([str(self.python), '-c', script, str(out), str(root), str(marker)], cwd=self.host, env=env, text=True, capture_output=True, timeout=100)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn('HOST_OK', proc.stdout)
            import time
            time.sleep(5)
            self.assertFalse(delayed.exists(), 'Windows job failed to terminate server descendant')
