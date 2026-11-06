import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import HTTPServer, BaseHTTPRequestHandler
from unittest.mock import patch
from generators import rest_safe


class MutationTests(unittest.TestCase):
    def test_real_host_gating_and_loopback(self):
        calls = []
        class Handler(BaseHTTPRequestHandler):
            def handle_mutation(self):
                calls.append((self.command, self.path, self.rfile.read(int(self.headers.get('Content-Length', '0'))), self.headers.get('Content-Type'), self.headers.get('X-Trace-Id')))
                self.send_response(200); self.end_headers(); self.wfile.write(b'{}')
            do_PUT = do_PATCH = do_DELETE = handle_mutation
            def log_message(self, *args): pass
        server = HTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); src = root/'src'; src.mkdir()
            schema = {'type':'object','properties':{'name':{'type':'string'}},'required':['name']}
            explicit_true = dict(schema, additionalProperties=True)
            explicit_false = dict(schema, additionalProperties=False)
            body = {'content':{'application/json':{'schema':schema}}}
            header = {'name':'X-Trace-Id','in':'header','schema':{'type':'string'}}
            paths = {'/item': {'put':{'operationId':'putItem','requestBody':dict(body, required=True),'parameters':[header]}, 'patch':{'operationId':'patchItem','requestBody':body}, 'delete':{'operationId':'deleteItem'}, 'post':{'operationId':'postItem'}}}
            paths['/true'] = {'patch': {'operationId':'trueItem', 'requestBody':{'content':{'application/json':{'schema':explicit_true}}}}}
            paths['/false'] = {'put': {'operationId':'falseItem', 'requestBody':{'content':{'application/json':{'schema':explicit_false}}}}}
            (src/'openapi.json').write_text(json.dumps({'openapi':'3.0.0','servers':[{'url':'https://example.invalid/api'}], 'paths':paths}))
            out = rest_safe.generate(src, root/'out', 'f', {})['output_path']
            data=root/'data'; data.mkdir()
            (data/'openapi-authorization.json').write_text(json.dumps({'base_url':'http://127.0.0.1:%s'%server.server_port,'operations':['PUT /api/item','PATCH /api/item','DELETE /api/item','PATCH /api/true','PUT /api/false']}))
            code = '''import os, sys
from pathlib import Path
from hermes_cli.agent_plugins import load_agent_plugin
from tools import mcp_tool_discovery, mcp_tool_lifecycle
from tools.terminal_tool import set_approval_callback
from model_tools import handle_function_call
p=load_agent_plugin(Path(sys.argv[1]),Path(sys.argv[2])); assert not p.diagnostics, p.diagnostics
name,config=next(iter(p.mcp_servers.items())); assert config['trust']=='untrusted'
try:
 names=mcp_tool_discovery.register_mcp_servers({name:config})
 def tool(s): return next(n for n in names if n.endswith('_'+s+'item'))
 os.environ['HERMES_SINGLE_QUERY_SESSION']='1'
 assert 'not approve' in handle_function_call(tool('put'), {'body':{'name':'ignored'}}).lower()
 os.environ.pop('HERMES_SINGLE_QUERY_SESSION'); os.environ['HERMES_INTERACTIVE']='1'
 set_approval_callback(lambda *a,**k:'n')
 assert 'not approve' in handle_function_call(tool('delete'), {}).lower()
 assert 'not approve' in handle_function_call(tool('patch'), {'body':{'name':'denied'}}).lower()
 set_approval_callback(lambda *a,**k:'once')
 extra={'nested':[None, True, 2, 1.5, {'text':'snow ☃'}]}
 answer=handle_function_call(tool('put'), {'body':{'name':'snow ☃','extra':extra},'X-Trace-Id':'trace'}); assert 'true' in answer.lower(), answer
 answer=handle_function_call(tool('true'), {'body':{'name':'yes','extra':extra}}); assert 'true' in answer.lower(), answer
 answer=handle_function_call(tool('false'), {'body':{'name':'no','extra':extra}}); assert 'false' in answer.lower() or 'invalid' in answer.lower(), answer
 answer=handle_function_call(tool('put'), {'body':{'extra':extra}}); assert 'false' in answer.lower() or 'required' in answer.lower(), answer
 assert 'true' in handle_function_call(tool('patch'), {}).lower()
 assert 'true' in handle_function_call(tool('patch'), {'body':{'name':'x'}}).lower()
 assert 'true' in handle_function_call(tool('delete'), {}).lower()
 assert 'false' in handle_function_call(tool('post'), {}).lower()
 answer=handle_function_call(tool('put'), {}); assert 'false' in answer.lower() or 'required' in answer.lower(), answer
 answer=handle_function_call(tool('put'), {'body':{'name':3}}); assert 'false' in answer.lower() or 'invalid' in answer.lower(), answer
 print('HOST_OK')
finally:
 set_approval_callback(None); mcp_tool_lifecycle.shutdown_mcp_servers()
'''
            env=dict(os.environ, HERMES_HOME=str(root/'home'), TMPDIR=tmp, TMP=tmp, TEMP=tmp, PYTHONDONTWRITEBYTECODE='1')
            env['PATH']=str(Path(sys.executable).parent)+os.pathsep+env.get('PATH','')
            for key in ('PYTHONPATH','HERMES_SINGLE_QUERY_SESSION','HERMES_GATEWAY_SESSION','HERMES_SESSION_PLATFORM','HERMES_CRON_SESSION','HERMES_YOLO_MODE'): env.pop(key,None)
            proc=subprocess.run([sys.executable,'-c',code,out,str(data)], cwd=str(Path(sys.prefix).parent),env=env,capture_output=True,text=True,timeout=90)
            self.assertEqual(proc.returncode,0,proc.stdout+proc.stderr)
            self.assertIn('HOST_OK',proc.stdout)
            extra={'nested':[None, True, 2, 1.5, {'text':'snow ☃'}]}
            encode=lambda obj: json.dumps(obj, ensure_ascii=True).encode('utf-8')
            self.assertEqual(calls,[('PUT','/api/item',encode({'name':'snow ☃','extra':extra}),'application/json','trace'),('PATCH','/api/true',encode({'name':'yes','extra':extra}),'application/json',None),('PATCH','/api/item',b'',None,None),('PATCH','/api/item',encode({'name':'x'}),'application/json',None),('DELETE','/api/item',b'',None,None)])

    def test_additional_values_and_unsupported_schemas(self):
        import runpy
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root=Path(tmp); source=root/'openapi.json'
            def spec(schema):
                return {'openapi':'3.0.0','paths':{'/x':{'patch':{'operationId':'x','requestBody':{'content':{'application/json':{'schema':schema}}}}}}}
            schema={'type':'object','properties':{'name':{'type':'string'}},'required':['name']}
            for invalid in ({'type':'string'}, 1, None, 'true'):
                source.write_text(json.dumps(spec(dict(schema, additionalProperties=invalid))))
                with patch.object(Path,'mkdir',side_effect=AssertionError('output reached')):
                    with self.assertRaises(ValueError): rest_safe.generate(root,root/'out','demo',{})
            nested=dict(schema, properties={'name':{'type':'object','properties':{'x':{'type':'string'}}}})
            source.write_text(json.dumps(spec(nested)))
            with patch.object(Path,'mkdir',side_effect=AssertionError('output reached')):
                with self.assertRaises(ValueError): rest_safe.generate(root,root/'out','demo',{})
            source.write_text(json.dumps(spec(schema)))
            out=rest_safe.generate(root,root/'out','demo',{})['output_path']
            data=root/'data'; data.mkdir()
            (data/'openapi-authorization.json').write_text(json.dumps({'base_url':'http://127.0.0.1:1234','operations':['PATCH /x']}))
            with patch.dict(os.environ, {'PLUGIN_DATA':str(data)}): server=runpy.run_path(str(Path(out)/'server.py'))
            self.assertNotIn('additionalProperties',server['input_schema'](server['OPERATIONS'][0])['properties']['body'])
            sink=Mock(side_effect=AssertionError('network reached'))
            with patch.dict(server['invoke'].__globals__, {'build_opener':sink}):
                for extra in (float('nan'), float('inf'), {1:'bad'}, (1,2), 'x'*65537):
                    self.assertFalse(server['invoke'](server['OPERATIONS'][0],{'body':{'name':'ok','extra':extra}})['success'])
                sink.assert_not_called()

    def test_post_allowlist_does_not_authorize_put(self):
        import runpy
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root=Path(tmp)
            (root/'openapi.json').write_text(json.dumps({'openapi':'3.0.0','paths':{'/x':{'put':{'operationId':'putX'}}}}))
            out=rest_safe.generate(root,root/'out','demo',{})['output_path']
            data=root/'data'; data.mkdir()
            (data/'openapi-authorization.json').write_text(json.dumps({'base_url':'http://127.0.0.1:1234','operations':['POST /x']}))
            with patch.dict(os.environ, {'PLUGIN_DATA':str(data)}): namespace=runpy.run_path(str(Path(out)/'server.py'))
            sink=Mock(side_effect=AssertionError('network reached'))
            with patch.dict(namespace['invoke'].__globals__, {'build_opener':sink}):
                self.assertFalse(namespace['invoke'](namespace['OPERATIONS'][0],{})['success'])
                sink.assert_not_called()

    def test_unsupported_methods_and_media_rejected_before_output(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root=Path(tmp)
            for method, op in [('options',{'operationId':'x'}),('head',{'operationId':'x'}),('put',{'operationId':'x','requestBody':{'content':{'multipart/form-data':{'schema':{'type':'object'}}}}})]:
                (root/'openapi.json').write_text(json.dumps({'openapi':'3.0.0','paths':{'/x':{method:op}}}))
                with self.subTest(method=method), patch.object(Path,'mkdir',side_effect=AssertionError('write reached')):
                    with self.assertRaises(ValueError): rest_safe.generate(root,root/'out','demo',{})
