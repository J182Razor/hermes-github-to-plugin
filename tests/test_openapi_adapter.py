import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

import detector
from generators import rest_safe


class OpenAPIAdapterTests(unittest.TestCase):
    def test_mutation_methods_generate_with_optional_and_required_bodies(self):
        schema = {'type':'object','properties':{'name':{'type':'string'}}}
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp)
            paths = {'/x': {method: {'operationId': method, **({'requestBody': {'required': True, 'content': {'application/json': {'schema': schema}}}} if method == 'put' else {})} for method in ('put', 'patch', 'delete')}}
            (root/'openapi.json').write_text(json.dumps({'openapi':'3.0.0','paths':paths}))
            result = rest_safe.generate(root, root/'out', 'demo', {})
            self.assertTrue(Path(result['output_path'], 'server.py').exists())

    def test_server_and_unknown_root_fields_rejected_before_output(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); source = root / 'openapi.json'
            for field, value, reason in [('servers', [{'url':'http://example.invalid/api/v3'}], 'unsafe OpenAPI root server URL'),
                                          ('x-unknown', True, 'unsupported OpenAPI root field')]:
                with self.subTest(field=field):
                    source.write_text(json.dumps({'openapi':'3.0.4','paths':{'/pet':{'get':{'operationId':'getPet'}}},field:value}))
                    with patch.object(Path, 'mkdir', side_effect=AssertionError('write sink reached')):
                        with self.assertRaisesRegex(ValueError, reason): rest_safe.generate(root, root/'out', 'demo', {})

    def test_hermes_manager_registry_loopback(self):
        import subprocess, sys, threading
        from http.server import HTTPServer, BaseHTTPRequestHandler
        from unittest.mock import patch
        import tools as converter
        calls = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                calls.append((self.path, self.headers.get('X-Trace-Id')))
                if self.path.startswith('/redirect'):
                    self.send_response(302); self.send_header('Location', '/forbidden'); self.end_headers(); return
                self.send_response(200); self.end_headers(); self.wfile.write(b'{}')
            def log_message(self, *args): pass
        server = HTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); src = root / 'src'; src.mkdir()
            spec = {'openapi':'3.0.4', 'info': {'title':'Synthetic Pet Store','version':'1.0'},
                'externalDocs': {'description':'Documentation','url':'https://example.invalid/docs'},
                'tags': [{'name':'pet','description':'Pets', 'externalDocs': {'description':'More','url':'https://example.invalid/pets'}}],
                'components': {
                'schemas': {'text/~value': {'type':'string'}},
                'parameters': {'Id': {'name':'id','in':'path','required':True,'schema':{'$ref':'#/components/schemas/text~1~0value'}},
                               'Query': {'name':'q','in':'query','schema':{'$ref':'#/components/schemas/text~1~0value'}}},
                'responses': {'OK': {'description':'ok'}}},
                'paths': {'/item/{id}':{'get':{'operationId':'getItem','parameters':[
                {'$ref':'#/components/parameters/Id'}, {'$ref':'#/components/parameters/Query'},
                {'name':'X-Trace-Id','in':'header','schema':{'type':'string'}}],
                'responses': {'200': {'$ref':'#/components/responses/OK'}}}},
                '/redirect':{'get':{'operationId':'redirect'}}}}
            (src / 'openapi.yaml').write_text(__import__('yaml').safe_dump(spec), encoding='utf-8')
            report = detector.detect_type(str(src)); self.assertEqual(report['type'], 'rest')
            report['repo_path'] = str(src)
            result = json.loads(converter._convert_detected(report, '', 'fixture', str(root / 'plugins')))
            self.assertTrue(result['success'], result)
            base = 'http://127.0.0.1:%s' % server.server_port
            data = root / 'data'; data.mkdir()
            (data / 'openapi-authorization.json').write_text(json.dumps({'base_url':base, 'operations':['GET /item/{id}', 'GET /redirect']}))
            code = '''import os, sys
from pathlib import Path
from hermes_cli.agent_plugins import load_agent_plugin
from tools import mcp_tool_discovery, mcp_tool_lifecycle
from tools.terminal_tool import set_approval_callback
from model_tools import handle_function_call
p=load_agent_plugin(Path(sys.argv[1]),Path(sys.argv[2])); assert not p.diagnostics, p.diagnostics
name, config = next(iter(p.mcp_servers.items())); assert config['trust']=='untrusted'
try:
 names=mcp_tool_discovery.register_mcp_servers({name:config})
 item=next(n for n in names if n.endswith('_getitem')); redirect=next(n for n in names if n.endswith('_redirect'))
 os.environ['HERMES_SINGLE_QUERY_SESSION']='1'
 assert 'not approve' in handle_function_call(item, {'id':'abc'}).lower()
 os.environ.pop('HERMES_SINGLE_QUERY_SESSION'); os.environ['HERMES_INTERACTIVE']='1'
 set_approval_callback(lambda *a, **k: 'n')
 assert 'not approve' in handle_function_call(item, {'id':'abc'}).lower()
 set_approval_callback(lambda *a, **k: 'once')
 assert 'rejected' in handle_function_call(item, {'id':'a/b'}).lower()
 answer=handle_function_call(item, {'id':'abc','q':'x & y','X-Trace-Id':'trace'}); assert 'true' in answer.lower(), answer
 answer=handle_function_call(redirect, {}); assert 'false' in answer.lower(), answer
 print('HOST_OK')
finally:
 set_approval_callback(None); mcp_tool_lifecycle.shutdown_mcp_servers()
'''
            env = dict(os.environ, HERMES_HOME=str(root / 'home'), TMPDIR=tmp, TMP=tmp, TEMP=tmp, PYTHONDONTWRITEBYTECODE='1')
            env['PATH'] = str(Path(sys.executable).parent) + os.pathsep + env.get('PATH','')
            for key in ('PYTHONPATH','HERMES_SINGLE_QUERY_SESSION','HERMES_GATEWAY_SESSION','HERMES_SESSION_PLATFORM','HERMES_CRON_SESSION','HERMES_YOLO_MODE'): env.pop(key, None)
            proc = subprocess.run([sys.executable, '-c', code, result['output_path'], str(data)], cwd=str(Path(sys.prefix).parent), env=env, capture_output=True, text=True, timeout=90)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn('HOST_OK', proc.stdout)
            self.assertEqual(calls, [('/item/abc?q=x+%26+y','trace'), ('/redirect',None)])

    def test_reference_rejections_before_output_and_positive_control(self):
        from unittest.mock import patch
        import copy
        base = {'openapi':'3.0.0', 'components':{'schemas':{'S':{'type':'string'}}},
                'paths':{'/x':{'get':{'operationId':'x', 'parameters':[
                    {'name':'q','in':'query','schema':{'$ref':'#/components/schemas/S'}}]}}}}
        cases = []
        for ref in ('https://example.invalid/a', 'file:///secret', '#/components/schemas/Missing',
                    '#/components/schemas/S~2', '#/components/schemas/S%2F',
                    '#/components/schemas/S/type', '#/paths', '#/components/schemas/S/'):
            spec = copy.deepcopy(base); spec['paths']['/x']['get']['parameters'][0]['schema'] = {'$ref':ref}; cases.append(spec)
        spec = copy.deepcopy(base); spec['components']['schemas']['S'] = {'$ref':'#/components/schemas/S'}; cases.append(spec)
        spec = copy.deepcopy(base); spec['components']['schemas'].update({'S':{'$ref':'#/components/schemas/T'}, 'T':{'$ref':'#/components/schemas/S'}}); cases.append(spec)
        spec = copy.deepcopy(base); spec['paths']['/x']['get']['parameters'][0]['schema']['type'] = 'string'; cases.append(spec)

        spec = copy.deepcopy(base); spec['components']['schemas']['S'] = {'type':'array','items':{'type':'string'}}; cases.append(spec)
        spec = copy.deepcopy(base); spec['components']['schemas']['S'] = {'type':'string','format':'uuid'}; cases.append(spec)
        spec = copy.deepcopy(base); spec['components']['schemas']['S'] = {'values':[0] * 4097}; cases.append(spec)
        spec = copy.deepcopy(base); spec['components']['schemas']['S'] = {'nested':{}}; node = spec['components']['schemas']['S']['nested']
        for _ in range(70): node['nested'] = {}; node = node['nested']
        cases.append(spec)
        spec = copy.deepcopy(base); schemas = spec['components']['schemas']; schemas['S'] = {'blob':'x' * 50000}
        for i in range(8): schemas['R'+str(i)] = {'a':{'$ref':'#/components/schemas/' + ('S' if i == 0 else 'R'+str(i-1))}, 'b':{'$ref':'#/components/schemas/' + ('S' if i == 0 else 'R'+str(i-1))}}
        spec['paths']['/x']['get']['parameters'][0]['schema'] = {'$ref':'#/components/schemas/R7'}
        cases.append(spec)
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); source = root/'openapi.json'
            source.write_text(json.dumps(base))
            with patch.object(Path, 'mkdir', side_effect=RuntimeError('observed output sink')) as sink:
                with self.assertRaisesRegex(RuntimeError, 'observed output sink'): rest_safe.generate(root, root/'out', 'demo', {})
                sink.assert_called_once()
            for i, spec in enumerate(cases):
                with self.subTest(case=i):
                    source.write_text(json.dumps(spec))
                    with patch.object(Path, 'mkdir', side_effect=AssertionError('write sink reached')) as sink, \
                         patch('urllib.request.urlopen', side_effect=AssertionError('network sink reached')) as network:
                        with self.assertRaises(ValueError): rest_safe.generate(root, root/'out', 'demo', {})
                        sink.assert_not_called(); network.assert_not_called()

    def test_duplicate_keys_and_aliases_rejected_before_output(self):
        from unittest.mock import patch
        cases = [('openapi.yaml', 'openapi: 3.1.0\nopenapi: 3.0.0\npaths:\n  /x:\n    get:\n      operationId: x\n'),
                 ('openapi.yaml', 'openapi: 3.0.0\npaths: &p {}\nother: *p\n'),
                 ('openapi.json', '{"openapi":"3.1.0","openapi":"3.0.0","paths":{"/x":{"get":{"operationId":"x"}}}}')]
        for filename, content in cases:
            with self.subTest(content=content), tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
                root=Path(tmp); (root / filename).write_text(content)
                with patch.object(Path, 'mkdir', side_effect=AssertionError('write sink reached')):
                    with self.assertRaises(ValueError): rest_safe.generate(root, root / 'out', 'demo', {})

    def test_unsupported_constructs_rejected_before_writes(self):
        from unittest.mock import patch
        bad_params = [
            {'name':'Authorization','in':'header','schema':{'type':'string'}},
            {'name':'Proxy-Authorization','in':'header','schema':{'type':'string'}},
            {'name':'q','in':'query','schema':{'type':'array','items':{'type':'string'}}},
            {'name':'q','in':'query','style':'spaceDelimited','schema':{'type':'string'}},
            {'name':'q','in':'query','schema':{'type':'string','format':'uuid'}},
            {'name':'session','in':'cookie','schema':{'type':'string'}}]
        operations = [{'operationId':'x','parameters':[p]} for p in bad_params]
        operations += [{'operationId':'x','requestBody':{}},
                       {'operationId':'x','servers':[]}, {'operationId':'x','responses':{'200':{'$ref':'#/x'}}}]
        for op in operations:
            with self.subTest(op=op), tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
                root=Path(tmp); (root/'openapi.json').write_text(json.dumps({'openapi':'3.0.0','paths':{'/x':{'get':op}}}))
                with patch.object(Path, 'mkdir', side_effect=AssertionError('write sink reached')):
                    with self.assertRaises(ValueError): rest_safe.generate(root, root/'out', 'demo', {})

    def test_control_characters_in_approved_base_fail_before_network(self):
        import runpy
        from unittest.mock import patch, Mock
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root=Path(tmp); (root/'openapi.json').write_text(json.dumps({'openapi':'3.0.0','paths':{'/x':{'get':{'operationId':'x'}}}}))
            out=Path(rest_safe.generate(root,root/'out','demo',{})['output_path']); data=root/'data'; data.mkdir()
            (data/'openapi-authorization.json').write_text(json.dumps({'base_url':'http://127.0.0.1\n:1234','operations':['GET /x']}))
            with patch.dict(os.environ, {'PLUGIN_DATA':str(data)}): server=runpy.run_path(str(out/'server.py'))
            sink=Mock(side_effect=AssertionError('network reached'))
            with patch.dict(server['invoke'].__globals__, {'build_opener':sink}):
                self.assertFalse(server['invoke'](server['OPERATIONS'][0],{})['success']); sink.assert_not_called()

    def test_yaml_header_supported(self):
        import runpy
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root=Path(tmp); (root/'openapi.yaml').write_text('openapi: 3.0.0\npaths:\n  /item:\n    get:\n      operationId: getItem\n      parameters:\n        - name: X-Trace-Id\n          in: header\n          schema:\n            type: string\n')
            self.assertEqual(detector.detect_type(str(root))['type'],'rest')
            output=rest_safe.generate(root,root/'out','demo',{})['output_path']
            server=runpy.run_path(str(Path(output)/'server.py'))
            self.assertEqual(server['OPERATIONS'][0][2][0]['in'],'header')

    def test_reject_root_servers_and_unsupported_dialect(self):
        from unittest.mock import patch
        for field, value in [('servers', [{'url': 'https://example.invalid'}, {'url': 'https://other.invalid'}]), ('openapi', '3.1.0')]:
            with self.subTest(field=field), tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
                root = Path(tmp)
                spec = {'openapi':'3.0.0', 'paths':{'/x':{'get':{'operationId':'x'}}}}
                spec[field] = value
                (root / 'openapi.json').write_text(json.dumps(spec))
                with patch.object(Path, 'mkdir', side_effect=AssertionError('write sink reached')):
                    with self.assertRaises(ValueError): rest_safe.generate(root, root / 'out', 'demo', {})

    def test_parameterized_request_and_rejection_on_loopback(self):
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from unittest.mock import patch
        import threading, runpy
        calls=[]
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                calls.append(self.path); self.send_response(200); self.end_headers(); self.wfile.write(b'{}')
            def log_message(self,*args): pass
        http=HTTPServer(('127.0.0.1',0),Handler); threading.Thread(target=http.serve_forever,daemon=True).start()
        self.addCleanup(http.server_close); self.addCleanup(http.shutdown)
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root=Path(tmp); src=root/'src'; src.mkdir(); data=root/'data'; data.mkdir()
            spec={'openapi':'3.0.0','paths':{'/item/{id}':{'get':{'operationId':'getItem','parameters':[{'name':'id','in':'path','required':True,'schema':{'type':'string'}},{'name':'q','in':'query','schema':{'type':'string'}}]}}}}
            (src/'openapi.json').write_text(json.dumps(spec)); out=Path(rest_safe.generate(src,root/'out','fixture',{})['output_path'])
            (data/'openapi-authorization.json').write_text(json.dumps({'base_url':f'http://127.0.0.1:{http.server_port}','operations':['GET /item/{id}']}))
            with patch.dict(os.environ, {'PLUGIN_DATA':str(data)}): server=runpy.run_path(str(out/'server.py'))
            invoke=server['invoke']; op=server['OPERATIONS'][0]
            self.assertTrue(invoke(op,{'id':'abc','q':'x & y'})['success'])
            self.assertFalse(invoke(op,{'id':42})['success']); self.assertFalse(invoke(op,{'id':'a/b'})['success'])
            self.assertFalse(invoke(op,{'id':'x','base_url':'http://127.0.0.1'})['success'])
            self.assertEqual(calls,['/item/abc?q=x+%26+y'])

    def test_parameterized_openapi_detects_and_registers_typed_schema(self):
        import runpy
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root=Path(tmp); src=root/'source'; src.mkdir()
            spec={'openapi':'3.0.0','paths':{'/items/{id}':{'get':{'operationId':'getItem','parameters':[{'name':'id','in':'path','required':True,'schema':{'type':'string'}},{'name':'limit','in':'query','schema':{'type':'integer'}}]}}}}
            (src/'openapi.json').write_text(json.dumps(spec)); report=detector.detect_type(str(src)); self.assertEqual(report['type'],'rest')
            out=Path(rest_safe.generate(src,root/'out','sample',report)['output_path']); server=runpy.run_path(str(out/'server.py'))
            schema=server['input_schema'](server['OPERATIONS'][0])
            self.assertEqual(schema['properties']['id']['type'],'string'); self.assertEqual(schema['properties']['limit']['type'],'integer'); self.assertIn('id',schema['required'])


if __name__ == '__main__':
    unittest.main()
