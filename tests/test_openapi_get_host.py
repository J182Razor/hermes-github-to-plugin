from host_test_config import host_test_config
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import detector
import tools


class GetHostTests(unittest.TestCase):
    def test_plain_get_real_host_approval_and_validation(self):
        host, host_python = host_test_config(windows_only=False)
        calls = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                calls.append((self.path, self.headers.get('X-Trace-Id')))
                self.send_response(200); self.end_headers(); self.wfile.write(b'{"ok":true}')
            def log_message(self, *args): pass
        http = HTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.server_close); self.addCleanup(http.shutdown)
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); src = root/'src'; src.mkdir(); data = root/'data'; data.mkdir()
            spec = {'openapi':'3.0.0', 'components':{'schemas':{'S':{'type':'string'}}, 'parameters':{'Id':{'name':'id','in':'path','required':True,'schema':{'$ref':'#/components/schemas/S'}}}, 'responses':{'OK':{'description':'ok'}}}, 'paths':{'/x/{id}':{'get':{'operationId':'item','parameters':[{'$ref':'#/components/parameters/Id'}, {'name':'q','in':'query','schema':{'type':'string'}}, {'name':'X-Trace-Id','in':'header','schema':{'type':'string'}}], 'responses':{'200':{'$ref':'#/components/responses/OK'}}}}, '/blocked':{'get':{'operationId':'blocked'}}}}
            (src/'openapi.json').write_text(json.dumps(spec))
            report = detector.detect_type(str(src)); self.assertEqual(report['type'], 'rest'); report['repo_path'] = str(src)
            result = json.loads(tools._convert_detected(report, '', 'demo', str(root/'out'))); self.assertTrue(result['success'], result)
            (data/'openapi-authorization.json').write_text(json.dumps({'base_url':f'http://127.0.0.1:{http.server_port}', 'operations':['GET /x/{id}']}))
            code = r'''
import os, sys
from pathlib import Path
from hermes_cli.agent_plugins import load_agent_plugin
from tools import mcp_tool, mcp_tool_discovery, mcp_tool_lifecycle
from tools.terminal_tool import set_approval_callback
from model_tools import handle_function_call
p=load_agent_plugin(Path(sys.argv[1]),Path(sys.argv[2])); assert not p.diagnostics, p.diagnostics
name, config = next(iter(p.mcp_servers.items())); assert config['trust']=='untrusted'
try:
 names=mcp_tool_discovery.register_mcp_servers({name:config}); assert len(names)==2
 assert all(x is False for hints in mcp_tool._tool_read_only_hints.values() for x in hints.values())
 item=next(x for x in names if x.endswith('_item')); blocked=next(x for x in names if x.endswith('_blocked'))
 os.environ['HERMES_SINGLE_QUERY_SESSION']='1'
 assert 'not approve' in handle_function_call(item, {'id':'unattended'}).lower()
 os.environ.pop('HERMES_SINGLE_QUERY_SESSION'); os.environ['HERMES_INTERACTIVE']='1'
 set_approval_callback(lambda *a, **k: 'n')
 assert 'not approve' in handle_function_call(item, {'id':'denied'}).lower()
 set_approval_callback(lambda *a, **k: 'once')
 assert 'rejected' in handle_function_call(item, {'id':42}).lower()
 assert 'rejected' in handle_function_call(item, {'id':'..'}).lower()
 mcp_tool._server_error_counts.clear()
 assert 'rejected' in handle_function_call(item, {'id':'ok','q':3}).lower()
 assert 'rejected' in handle_function_call(item, {'id':'ok','X-Trace-Id':'bad\nvalue'}).lower()
 mcp_tool._server_error_counts.clear()
 assert 'rejected' in handle_function_call(blocked, {}).lower()
 result=handle_function_call(item, {'id':'abc','q':'x & y','X-Trace-Id':'trace'}); assert 'true' in result.lower(), result
 print('GET_HOST_OK')
finally:
 set_approval_callback(None); mcp_tool_lifecycle.shutdown_mcp_servers()
'''
            env = dict(os.environ, HERMES_HOME=str(root/'home'), TMPDIR=tmp, TMP=tmp, TEMP=tmp, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH='')
            env['PATH'] = str(host_python.parent) + os.pathsep + env.get('PATH','')
            for key in ('HERMES_SINGLE_QUERY_SESSION','HERMES_GATEWAY_SESSION','HERMES_SESSION_PLATFORM','HERMES_CRON_SESSION','HERMES_YOLO_MODE'): env.pop(key, None)
            proc = subprocess.run([str(host_python), '-c', code, result['output_path'], str(data)], cwd=host, env=env, capture_output=True, text=True, timeout=90)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn('GET_HOST_OK', proc.stdout)
            self.assertEqual(calls, [('/x/abc?q=x+%26+y', 'trace')])
