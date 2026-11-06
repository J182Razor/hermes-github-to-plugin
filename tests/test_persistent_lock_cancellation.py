from host_test_config import host_test_config
"""Cancellation while queued behind the generated persistent-call lock."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from generators import mcp_safe



class PersistentLockCancellationTests(unittest.TestCase):

    def test_cancel_waiter_does_not_reach_source_or_poison_session(self):
        host, host_python = host_test_config(windows_only=True)
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp)
            src = root / 'src'
            src.mkdir()
            source = '''import asyncio
from pathlib import Path
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types
root = Path(%r)
(root / 'started').touch()
count = 0
async def list_tools(ctx, params):
    return types.ListToolsResult(tools=[types.Tool(name='run', inputSchema={'type':'object'})])
async def call_tool(ctx, params):
    global count
    count += 1
    (root / ('received-' + params.arguments['label'])).touch()
    if params.arguments['label'] == 'first':
        while not (root / 'release').exists():
            await asyncio.sleep(.01)
    return types.CallToolResult(content=[types.TextContent(type='text', text='count=%%d' %% count)])
async def main():
    s = Server('fixture', on_list_tools=list_tools, on_call_tool=call_tool)
    async with stdio_server() as (r,w): await s.run(r,w,s.create_initialization_options())
if __name__ == '__main__': asyncio.run(main())
''' % str(root)
            (src / 'server.py').write_text(source, encoding='utf-8')
            (src / 'mcp.json').write_text(json.dumps({'mcpServers': {'fixture': {'command': 'python', 'args': ['server.py']}}}), encoding='utf-8')
            out = Path(mcp_safe.generate(src, root / 'out', 'fixture', {})['output_path'])
            script = r'''
import asyncio, importlib.util, sys
from pathlib import Path
from mcp import types
out, root = map(Path, sys.argv[1:])
consent = root / 'consent'
consent.write_text('I TRUST THIS SOURCE WITH PERSISTENT FULL OS AUTHORITY')
sys.path.insert(0, str(out))
sys.argv = ['server', str(consent)]
spec = importlib.util.spec_from_file_location('generated_server', out / 'server.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
class ObservedLock:
    def __init__(self, inner):
        self.inner = inner
        self.waiting = asyncio.Event()
    async def __aenter__(self):
        if self.inner.locked(): self.waiting.set()
        await self.inner.acquire()
    async def __aexit__(self, *args): self.inner.release()
server.lock = ObservedLock(server.lock)
async def wait_for_file(path):
    async with asyncio.timeout(15):
        while not path.exists(): await asyncio.sleep(.01)
def params(label):
    return types.CallToolRequestParams(name='invoke_reviewed_server', arguments={'toolName':'run', 'arguments':{'label':label}})
async def main():
    first = second = None
    try:
        assert not (root / 'started').exists()
        # Approval is simulated here; host-gate integration is covered separately.
        approvals = []
        async def approved(label):
            approvals.append(label)
            return await server.call_tool(None, params(label))
        first = asyncio.create_task(approved('first'))
        await wait_for_file(root / 'received-first')
        second = asyncio.create_task(approved('second'))
        await asyncio.wait_for(server.lock.waiting.wait(), 10)
        assert approvals == ['first', 'second'] and not (root / 'received-second').exists()
        second.cancel()
        try: await asyncio.wait_for(second, 5)
        except asyncio.CancelledError: pass
        else: raise AssertionError('waiting call was not cancelled')
        assert not (root / 'received-second').exists()
        (root / 'release').touch()
        result = await asyncio.wait_for(first, 15)
        assert 'count=1' in str(result), result
        third = await asyncio.wait_for(approved('third'), 15)
        assert 'count=2' in str(third), third
        assert (root / 'received-third').exists() and not (root / 'received-second').exists()
        assert approvals == ['first', 'second', 'third']
        print('LOCK_CANCEL_OK')
    finally:
        (root / 'release').touch()
        for task in (first, second):
            if task and not task.done(): task.cancel()
        await asyncio.gather(*(t for t in (first, second) if t), return_exceptions=True)
        server.manager.close()
asyncio.run(main())
'''
            env = dict(os.environ, HERMES_HOME=str(root / 'home'), TMPDIR=tmp, TMP=tmp, TEMP=tmp, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH='')
            env['PATH'] = str(host_python.parent) + os.pathsep + env.get('PATH', '')
            env.pop('PYTHONHOME', None)
            proc = subprocess.run([str(host_python), '-c', script, str(out), str(root)], cwd=host, env=env, text=True, capture_output=True, timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn('LOCK_CANCEL_OK', proc.stdout)
