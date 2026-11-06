import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

WORKER = Path(__file__).resolve().parents[1] / 'generators' / 'deferred_worker.py.txt'

class ConsoleWorkerTests(unittest.TestCase):
    def test_surrogate_rejected_before_source(self):
        source = b"print('SOURCE_MARKER')\ndef main(): pass\n"
        path = 'console_fixture/__init__.py'
        startup = ['\ud800']
        encoded = json.dumps(startup, ensure_ascii=True, separators=(',', ':')).encode()
        request = {'name':'anything', 'arguments':{}, 'package':{'module':'console_fixture','entrypoint':'main','identity':{'name':'g2h-console-test-identity','version':'1.02'},'files':{path:base64.b64encode(source).decode()}}, 'digests':{path:hashlib.sha256(source).hexdigest()}, 'requirements':[], 'startup_args':startup, 'startup_digest':hashlib.sha256(encoded).hexdigest()}
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as temp:
            worker = Path(temp)/'worker.py'
            worker.write_text(WORKER.with_name('package_envelope.py').read_text(encoding='utf-8')+'\n'+WORKER.read_text(encoding='utf-8'), encoding='utf-8')
            proc = subprocess.run([sys.executable,'-I',str(worker)], input=json.dumps(request)+'\n', text=True, capture_output=True, timeout=20)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn('invalid startup arguments', proc.stderr)
            self.assertNotIn('SOURCE_MARKER', proc.stdout+proc.stderr)
            self.assertNotIn('\\ud800', proc.stdout+proc.stderr)

    def test_duplicate_worker_request_rejected_before_source(self):
        source = b"print('SOURCE_MARKER')\ndef main(): pass\n"
        path = 'console_fixture/__init__.py'
        request = {'name':'anything', 'arguments':{}, 'package':{'module':'console_fixture','entrypoint':'main','identity':{'name':'g2h-console-test-identity','version':'1.02'},'files':{path:base64.b64encode(source).decode()}}, 'digests':{path:hashlib.sha256(source).hexdigest()}, 'requirements':[]}
        frame = json.dumps(request)
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as temp:
            worker = Path(temp)/'worker.py'
            worker.write_text(WORKER.with_name('package_envelope.py').read_text(encoding='utf-8')+'\n'+WORKER.read_text(encoding='utf-8'), encoding='utf-8')
            for bad in (frame.replace('"name": "anything"', '"name": "anything", "name": "anything"'), frame.replace('"entrypoint": "main"', '"entrypoint": "main", "entrypoint": "main"'), frame.replace('"digests": {', '"digests": {"duplicate": "a", "duplicate": "b", ')):
                proc = subprocess.run([sys.executable,'-I',str(worker)], input=bad+'\n', text=True,capture_output=True,timeout=20)
                self.assertNotEqual(proc.returncode,0)
                self.assertIn('duplicate JSON key',proc.stderr)
                self.assertNotIn('SOURCE_MARKER',proc.stdout+proc.stderr)

    def test_persistent_console_worker(self):
        source = b'''import asyncio
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types
count = 0
async def list_tools(ctx, params):
    return types.ListToolsResult(tools=[types.Tool(name='increment', inputSchema={'type':'object'})])
async def call(ctx, params):
    global count
    count += 1
    return types.CallToolResult(content=[types.TextContent(type='text', text='count=%s' % count)])
async def serve():
    server = Server('fixture', on_call_tool=call, on_list_tools=list_tools)
    async with stdio_server() as (r,w):
        await server.run(r,w,server.create_initialization_options())
def main(): asyncio.run(serve())
'''
        path = 'console_fixture/__init__.py'
        request = {'name':'increment', 'arguments':{}, 'package':{'module':'console_fixture','entrypoint':'main','identity':{'name':'g2h-console-test-identity','version':'1.02'},'files':{path:base64.b64encode(source).decode()}}, 'digests':{path:hashlib.sha256(source).hexdigest()}, 'requirements':[], 'startup_args':[], 'startup_digest':hashlib.sha256(b'[]').hexdigest()}
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as temp:
            worker = Path(temp) / 'worker.py'
            worker.write_text(WORKER.with_name('package_envelope.py').read_text(encoding='utf-8') + '\n' + WORKER.read_text(encoding='utf-8'), encoding='utf-8')
            proc = subprocess.run([sys.executable, '-I', str(worker)], input=(json.dumps(request)+'\n')*2, text=True, capture_output=True, timeout=40)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        responses = [json.loads(line) for line in proc.stdout.splitlines()]
        self.assertEqual(len(responses), 2)
        self.assertTrue(all(x['success'] for x in responses), responses)
        self.assertIn('count=1', json.dumps(responses[0]))
        self.assertIn('count=2', json.dumps(responses[1]))

if __name__ == '__main__': unittest.main()
