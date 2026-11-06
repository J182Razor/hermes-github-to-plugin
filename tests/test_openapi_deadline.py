import asyncio
import json
import os
import runpy
import tempfile
import threading
import time
import subprocess
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from generators import rest_safe


class DeadlineTests(unittest.TestCase):
    def test_slow_dribble_has_wall_clock_deadline(self):
        reached = threading.Event()
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                reached.set()
                self.send_response(200); self.end_headers()
                for _ in range(100):
                    try:
                        self.wfile.write(b'x'); self.wfile.flush()
                        time.sleep(.1)
                    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                        break
            def log_message(self, *args): pass
        http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=http.serve_forever, daemon=True); thread.start()
        self.addCleanup(http.server_close); self.addCleanup(http.shutdown)
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); src = root/'src'; src.mkdir(); data = root/'data'; data.mkdir()
            (src/'openapi.json').write_text(json.dumps({'openapi':'3.0.0','paths':{'/x':{'get':{'operationId':'x'}}}}))
            out = Path(rest_safe.generate(src, root/'out', 'demo', {})['output_path'])
            (data/'openapi-authorization.json').write_text(json.dumps({'base_url':'http://127.0.0.1:%s' % http.server_port,'operations':['GET /x']}))
            with patch.dict(os.environ, {'PLUGIN_DATA':str(data)}):
                server = runpy.run_path(str(out/'server.py'))
                g = server['call_tool'].__globals__
                g['REQUEST_DEADLINE'] = 3.0
                processes = []
                real_popen = subprocess.Popen
                def capture(*args, **kwargs):
                    process = real_popen(*args, **kwargs); processes.append(process); return process
                start = time.monotonic()
                with patch.object(subprocess, 'Popen', side_effect=capture):
                    result = asyncio.run(server['call_tool'](None, SimpleNamespace(name=server['OPERATIONS'][0][0], arguments={})))
                elapsed = time.monotonic() - start
                self.assertTrue(reached.is_set(), 'fixture must receive a real request')
                self.assertTrue(result.is_error)
                self.assertLess(elapsed, 5)
                self.assertEqual(len(processes), 1)
                self.assertIsNotNone(processes[0].poll())

    def test_cancel_reaps_request_worker(self):
        reached = threading.Event()
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200); self.end_headers(); reached.set()
                for _ in range(100):
                    try:
                        self.wfile.write(b'x'); self.wfile.flush(); time.sleep(.1)
                    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError): break
            def log_message(self, *args): pass
        http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close); self.addCleanup(http.shutdown)
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); src = root/'src'; src.mkdir(); data = root/'data'; data.mkdir()
            (src/'openapi.json').write_text(json.dumps({'openapi':'3.0.0','paths':{'/x':{'get':{'operationId':'x'}}}}))
            out = Path(rest_safe.generate(src, root/'out', 'demo', {})['output_path'])
            (data/'openapi-authorization.json').write_text(json.dumps({'base_url':'http://127.0.0.1:%s' % http.server_port,'operations':['GET /x']}))
            with patch.dict(os.environ, {'PLUGIN_DATA':str(data)}):
                server = runpy.run_path(str(out/'server.py')); processes = []; real_popen = subprocess.Popen
                def capture(*args, **kwargs):
                    process = real_popen(*args, **kwargs); processes.append(process); return process
                async def exercise():
                    task = asyncio.create_task(server['call_tool'](None, SimpleNamespace(name=server['OPERATIONS'][0][0], arguments={})))
                    for _ in range(100):
                        if reached.is_set(): break
                        await asyncio.sleep(.05)
                    self.assertTrue(reached.is_set())
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError): await asyncio.wait_for(task, 3)
                    self.assertEqual(len(processes), 1)
                    self.assertIsNotNone(processes[0].poll())
                with patch.object(subprocess, 'Popen', side_effect=capture): asyncio.run(exercise())
