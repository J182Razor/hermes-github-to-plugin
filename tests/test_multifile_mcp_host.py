from host_test_config import host_test_config
"""End-to-end contract for persistent deferred MCP sessions on Windows."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from generators import mcp_safe



class PersistentMultiFileHostTests(unittest.TestCase):

    def test_module_calls_are_gated_stateful_and_tampering_stops_job(self):
        host, host_python = host_test_config(windows_only=True)
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp)
            src = root / 'src'
            src.mkdir()
            marker = root / 'started'
            delayed = root / 'descendant-delayed'
            ready = root / 'descendant-ready'
            child = "import time; from pathlib import Path; Path(%r).touch(); time.sleep(8); Path(%r).touch()" % (str(ready), str(delayed))
            source = '''import asyncio, subprocess, sys
from pathlib import Path
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types
Path(%r).touch()
count = 0
async def list_tools(ctx, params):
    return types.ListToolsResult(tools=[types.Tool(name='increment', inputSchema={'type':'object'})])
async def call_tool(ctx, params):
    global count
    count += 1
    if count == 1:
        subprocess.Popen([sys.executable, '-c', %r], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return types.CallToolResult(content=[types.TextContent(type='text', text='count=%%s' %% count)])
async def main():
    server = Server('fixture', on_list_tools=list_tools, on_call_tool=call_tool)
    async with stdio_server() as (r, w):
        await server.run(r, w, server.create_initialization_options())
if __name__ == '__main__': asyncio.run(main())
''' % (str(marker), child)
            package = src / 'example'
            package.mkdir()
            (package / '__init__.py').write_text('', encoding='utf-8')
            (package / 'helper.py').write_text('VALUE = 0\n', encoding='utf-8')
            (package / 'server.py').write_text('from .helper import VALUE\n' + source, encoding='utf-8')
            (src / 'mcp.json').write_text(json.dumps({'mcpServers': {'fixture': {'command': 'python', 'args': ['-m', 'example.server']}}}), encoding='utf-8')
            out = Path(mcp_safe.generate(src, root / 'out', 'fixture', {})['output_path'])
            script = r'''
import os, sys, time, subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from hermes_cli.agent_plugins import load_agent_plugin
from tools import mcp_tool_discovery, mcp_tool_lifecycle
from tools.terminal_tool import set_approval_callback
from model_tools import handle_function_call
out, root, marker = map(Path, sys.argv[1:])
p = load_agent_plugin(out, root / 'data')
assert not p.diagnostics, p.diagnostics
name, config = next(iter(p.mcp_servers.items()))
assert config['trust'] == 'untrusted'
args = {'toolName': 'increment', 'arguments': {}}
try:
    tools = mcp_tool_discovery.register_mcp_servers({name: config})
    assert len(tools) == 1, tools
    tool = tools[0]
    assert not marker.exists()
    os.environ['HERMES_SINGLE_QUERY_SESSION'] = '1'
    assert 'not approve' in handle_function_call(tool, args).lower()
    assert not marker.exists()
    os.environ.pop('HERMES_SINGLE_QUERY_SESSION')
    os.environ['HERMES_INTERACTIVE'] = '1'
    approvals = []
    def deny(*a, **kw):
        approvals.append('deny')
        return 'n'
    set_approval_callback(deny)
    assert 'not approve' in handle_function_call(tool, args).lower()
    assert approvals == ['deny'] and not marker.exists()
    def approve(*a, **kw):
        approvals.append('simulated approval')
        return 'once'
    set_approval_callback(approve)

    missing = handle_function_call(tool, args)
    assert 'opt-in' in missing.lower() and not marker.exists(), missing
    consent = root / 'data' / 'persistent-mcp-opt-in.txt'
    consent.parent.mkdir(parents=True, exist_ok=True)
    consent.write_text('invalid', encoding='utf-8')
    invalid = handle_function_call(tool, args)
    assert 'opt-in' in invalid.lower() and not marker.exists(), invalid
    consent.write_text('I TRUST THIS SOURCE WITH PERSISTENT FULL OS AUTHORITY', encoding='utf-8')
    first = handle_function_call(tool, args)
    assert 'count=1' in first, first
    set_approval_callback(deny)
    denied_later = handle_function_call(tool, args)
    assert 'not approve' in denied_later.lower(), denied_later
    assert marker.exists()
    set_approval_callback(approve)
    second = handle_function_call(tool, args)
    assert 'count=2' in second, second
    assert approvals == ['deny', 'simulated approval', 'simulated approval', 'simulated approval', 'deny', 'simulated approval'], approvals
    def concurrent_call(_):
        set_approval_callback(approve)
        try: return handle_function_call(tool, args)
        finally: set_approval_callback(None)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(concurrent_call, range(4)))
    assert all(any('count=%d' % n in r for r in results) for n in range(3, 7)), results
    ready = root / 'descendant-ready'
    deadline = time.monotonic() + 5
    while not ready.exists() and time.monotonic() < deadline: time.sleep(.05)
    assert ready.exists(), 'descendant did not start'
    assert not (root / 'descendant-delayed').exists(), 'descendant marker was already due'
    control = root / 'control'
    positive = subprocess.Popen([sys.executable, '-c', "import time; from pathlib import Path; time.sleep(1); Path(%r).touch()" % str(control)])
    try:
        (out / 'source' / 'example' / 'helper.py').write_text('tampered')
        result = handle_function_call(tool, args)
        assert 'digest mismatch' in result.lower(), result
        positive.wait(timeout=5)
        time.sleep(9)
        assert control.exists() and not (root / 'descendant-delayed').exists()
    finally:
        if positive.poll() is None: positive.kill(); positive.wait()
    print('PERSISTENT_HOST_OK')
finally:
    set_approval_callback(None)
    mcp_tool_lifecycle.shutdown_mcp_servers()
'''
            env = dict(os.environ, HERMES_HOME=str(root / 'home'), TMPDIR=tmp, TMP=tmp, TEMP=tmp, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH='')
            env['PATH'] = str(host_python.parent) + os.pathsep + env.get('PATH', '')
            for key in ('PYTHONHOME', 'HERMES_SINGLE_QUERY_SESSION', 'HERMES_GATEWAY_SESSION', 'HERMES_SESSION_PLATFORM', 'HERMES_CRON_SESSION', 'HERMES_YOLO_MODE'):
                env.pop(key, None)
            proc = subprocess.run([str(host_python), '-c', script, str(out), str(root), str(marker)], cwd=host, env=env, text=True, capture_output=True, timeout=100)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn('PERSISTENT_HOST_OK', proc.stdout)
