import asyncio
import json
import os
import runpy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from generators import rest_safe


class ReviewRegressions(unittest.TestCase):
    def test_unicode_path_confusables_rejected_before_request(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); data = root/'data'; data.mkdir()
            params = [{'name':name, 'in':'path', 'required':True, 'schema':{'type':'string'}} for name in ('x', 'y')]
            spec = {'openapi':'3.0.0','paths':{'/safe/{x}{y}/admin':{'get':{'operationId':'x','parameters':params}}}}
            (root/'openapi.json').write_text(json.dumps(spec))
            out = Path(rest_safe.generate(root, root/'out', 'demo', {})['output_path'])
            (data/'openapi-authorization.json').write_text(json.dumps({'base_url':'http://example.invalid','operations':['GET /safe/{x}{y}/admin']}))
            with patch.dict(os.environ, {'PLUGIN_DATA':str(data)}): server = runpy.run_path(str(out/'server.py'))
            g = server['invoke'].__globals__
            sink = Mock(side_effect=AssertionError('request sink'))
            with patch.dict(g, {'Request':sink}):
                for x, y in [('\uff0e','\uff0e'), ('%EF%BC%8E','%EF%BC%8E'), ('%252e','%252e'), ('.','.'), ('/','a'), ('\\\\','a'), ('\n','a'), ('é','a')]:
                    with self.subTest(x=x, y=y):
                        self.assertFalse(server['invoke'](server['OPERATIONS'][0], {'x':x, 'y':y})['success'])
                sink.assert_not_called()
                server['invoke'](server['OPERATIONS'][0], {'x':'safe-', 'y':'A_1~'})
                sink.assert_called_once()

    def test_unicode_non_path_arguments_remain_supported(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); data = root/'data'; data.mkdir()
            params = [{'name':'id','in':'path','required':True,'schema':{'type':'string'}}, {'name':'q','in':'query','schema':{'type':'string'}}, {'name':'X-Trace-Id','in':'header','schema':{'type':'string'}}]
            body = {'required':True,'content':{'application/json':{'schema':{'type':'object','properties':{'text':{'type':'string'}},'required':['text']}}}}
            spec = {'openapi':'3.0.0','paths':{'/safe/{id}':{'post':{'operationId':'x','parameters':params,'requestBody':body}}}}
            (root/'openapi.json').write_text(json.dumps(spec))
            out = Path(rest_safe.generate(root, root/'out', 'demo', {})['output_path'])
            (data/'openapi-authorization.json').write_text(json.dumps({'base_url':'http://example.invalid','operations':['POST /safe/{id}']}))
            with patch.dict(os.environ, {'PLUGIN_DATA':str(data)}): server = runpy.run_path(str(out/'server.py'))
            response = Mock(status=200); response.read.return_value = b'{}'
            opener = Mock(); opener.open.return_value.__enter__ = Mock(return_value=response); opener.open.return_value.__exit__ = Mock(return_value=False)
            with patch.dict(server['invoke'].__globals__, {'build_opener':Mock(return_value=opener)}):
                result = server['invoke'](server['OPERATIONS'][0], {'id':'abc-1', 'q':'漢字', 'X-Trace-Id':'café', 'body':{'text':'漢字'}})
            self.assertTrue(result['success'])
            req = opener.open.call_args.args[0]
            self.assertIn('q=%E6%BC%A2%E5%AD%97', req.full_url)
            self.assertEqual(req.get_header('X-trace-id'), 'café')
            self.assertEqual(json.loads(req.data), {'text':'漢字'})

    def test_substitution_cannot_create_dot_segment(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp)
            param = {'name':'x','in':'path','required':True,'schema':{'type':'string'}}
            spec = {'openapi':'3.0.0','paths':{'/safe/.{x}/admin':{'get':{'operationId':'x','parameters':[param]}}}}
            (root/'openapi.json').write_text(json.dumps(spec))
            out = Path(rest_safe.generate(root, root/'out', 'demo', {})['output_path'])
            data = root/'data'; data.mkdir()
            (data/'openapi-authorization.json').write_text(json.dumps({'base_url':'http://127.0.0.1','operations':['GET /safe/.{x}/admin']}))
            with patch.dict(os.environ, {'PLUGIN_DATA':str(data)}): server = runpy.run_path(str(out/'server.py'))
            g = server['invoke'].__globals__
            sink = Mock(side_effect=AssertionError('request sink'))
            with patch.dict(g, {'Request':sink}):
                for value in ('', '.', '%2e', '%252e', '/', '\\'):
                    self.assertFalse(server['invoke'](server['OPERATIONS'][0], {'x':value})['success'])
                sink.assert_not_called()
                server['invoke'](server['OPERATIONS'][0], {'x':'abc'})
                sink.assert_called_once()

    def test_substitution_guard_with_real_loopback(self):
        from http.server import BaseHTTPRequestHandler, HTTPServer
        import threading
        calls = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                calls.append(self.path); self.send_response(200); self.end_headers(); self.wfile.write(b'{}')
            def log_message(self, *args): pass
        http = HTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close); self.addCleanup(http.shutdown)
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); data = root/'data'; data.mkdir()
            param = {'name':'x','in':'path','required':True,'schema':{'type':'string'}}
            spec = {'openapi':'3.0.0','paths':{'/safe/.{x}/admin':{'get':{'operationId':'x','parameters':[param]}}}}
            (root/'openapi.json').write_text(json.dumps(spec))
            out = Path(rest_safe.generate(root,root/'out','demo',{})['output_path'])
            (data/'openapi-authorization.json').write_text(json.dumps({'base_url':f'http://127.0.0.1:{http.server_port}','operations':['GET /safe/.{x}/admin']}))
            with patch.dict(os.environ, {'PLUGIN_DATA':str(data)}): server = runpy.run_path(str(out/'server.py'))
            self.assertFalse(server['invoke'](server['OPERATIONS'][0], {'x':''})['success'])
            self.assertTrue(server['invoke'](server['OPERATIONS'][0], {'x':'abc'})['success'])
            self.assertEqual(calls, ['/safe/.abc/admin'])

    def test_unused_recursive_components_do_not_block_conversion(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp)
            spec = {'openapi':'3.0.0','components':{'schemas':{'Node':{'type':'object','properties':{'next':{'$ref':'#/components/schemas/Node'}}}}},'paths':{'/x':{'get':{'operationId':'x'}}}}
            (root/'openapi.json').write_text(json.dumps(spec))
            result = rest_safe.generate(root, root/'out', 'demo', {})
            self.assertTrue(Path(result['output_path'], 'server.py').is_file())

    def test_concurrency_overload_and_cancellation_reclaims_slot(self):
        import threading
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp)
            (root/'openapi.json').write_text(json.dumps({'openapi':'3.0.0','paths':{'/x':{'get':{'operationId':'x'}}}}))
            out = Path(rest_safe.generate(root, root/'out', 'demo', {})['output_path'])
            server = runpy.run_path(str(out/'server.py'))
            g = server['call_tool'].__globals__
            started = threading.Event(); lock = threading.Lock(); active = [0]; peak = [0]
            def slow(command, payload, timeout, cancel):
                with lock:
                    active[0] += 1; peak[0] = max(peak[0], active[0]); started.set()
                try:
                    while not cancel.wait(.01): pass
                    return 1, '', ''
                finally:
                    with lock: active[0] -= 1
            async def exercise():
                args = SimpleNamespace(name=server['OPERATIONS'][0][0], arguments={})
                tasks = [asyncio.create_task(server['call_tool'](None, args)) for _ in range(4)]
                for _ in range(100):
                    if peak[0] == 4: break
                    await asyncio.sleep(.01)
                self.assertEqual(peak[0], 4)
                overload = await asyncio.wait_for(server['call_tool'](None, args), .5)
                self.assertTrue(overload.is_error)
                self.assertIn('overload', overload.content[0].text)
                tasks[0].cancel()
                with self.assertRaises(asyncio.CancelledError): await asyncio.wait_for(tasks[0], 3)
                replacement = asyncio.create_task(server['call_tool'](None, args))
                for _ in range(100):
                    if active[0] == 4: break
                    await asyncio.sleep(.01)
                self.assertEqual(active[0], 4)
                self.assertLessEqual(peak[0], 4)
                for task in tasks[1:] + [replacement]: task.cancel()
                await asyncio.gather(*tasks[1:], replacement, return_exceptions=True)
                self.assertEqual(active[0], 0)
            with patch.dict(g, {'_run_contained':slow}): asyncio.run(exercise())

    def test_real_loopback_workers_are_bounded_and_reaped(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import subprocess
        import threading
        reached = threading.Event(); release = threading.Event(); lock = threading.Lock(); count = [0]
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                with lock:
                    count[0] += 1
                    if count[0] >= 4: reached.set()
                release.wait(15)
                try:
                    self.send_response(200); self.end_headers(); self.wfile.write(b'{}')
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError): pass
            def log_message(self, *args): pass
        http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close); self.addCleanup(http.shutdown); self.addCleanup(release.set)
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); data = root/'data'; data.mkdir()
            (root/'openapi.json').write_text(json.dumps({'openapi':'3.0.0','paths':{'/x':{'get':{'operationId':'x'}}}}))
            out = Path(rest_safe.generate(root, root/'out', 'demo', {})['output_path'])
            (data/'openapi-authorization.json').write_text(json.dumps({'base_url':f'http://127.0.0.1:{http.server_port}','operations':['GET /x']}))
            with patch.dict(os.environ, {'PLUGIN_DATA':str(data)}):
                server = runpy.run_path(str(out/'server.py')); g = server['call_tool'].__globals__; g['REQUEST_DEADLINE'] = 15
                processes = []; real = subprocess.Popen; process_lock = threading.Lock()
                def capture(*args, **kwargs):
                    process = real(*args, **kwargs)
                    with process_lock: processes.append(process)
                    return process
                async def exercise():
                    args = SimpleNamespace(name=server['OPERATIONS'][0][0], arguments={})
                    tasks = [asyncio.create_task(server['call_tool'](None,args)) for _ in range(4)]
                    try:
                        for _ in range(300):
                            if reached.is_set(): break
                            await asyncio.sleep(.05)
                        self.assertTrue(reached.is_set())
                        result = await asyncio.wait_for(server['call_tool'](None,args), 1)
                        self.assertTrue(result.is_error); self.assertIn('overload', result.content[0].text)
                        self.assertEqual(len(processes), 4)
                        tasks[0].cancel()
                        with self.assertRaises(asyncio.CancelledError): await asyncio.wait_for(tasks[0], 5)
                        self.assertEqual(sum(p.poll() is None for p in processes), 3)
                        replacement = asyncio.create_task(server['call_tool'](None,args)); tasks.append(replacement)
                        for _ in range(200):
                            if len(processes) == 5: break
                            await asyncio.sleep(.05)
                        self.assertEqual(len(processes), 5)
                        self.assertLessEqual(sum(p.poll() is None for p in processes), 4)
                    finally:
                        for task in tasks: task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
                        release.set()
                    self.assertTrue(all(p.poll() is not None for p in processes))
                with patch.object(subprocess, 'Popen', side_effect=capture): asyncio.run(exercise())

    def test_reachable_recursive_component_fails_before_output(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp)
            spec = {'openapi':'3.0.0','components':{'schemas':{'Node':{'$ref':'#/components/schemas/Node'}}},'paths':{'/x':{'get':{'operationId':'x','parameters':[{'name':'q','in':'query','schema':{'$ref':'#/components/schemas/Node'}}]}}}}
            (root/'openapi.json').write_text(json.dumps(spec))
            with patch.object(Path, 'mkdir', side_effect=AssertionError('write sink')) as sink:
                with self.assertRaisesRegex(ValueError, 'circular'): rest_safe.generate(root, root/'out', 'demo', {})
                sink.assert_not_called()
