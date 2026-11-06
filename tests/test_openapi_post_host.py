from host_test_config import host_test_config
import json, os, tempfile, unittest
from pathlib import Path
from generators import rest_safe

class PostGeneration(unittest.TestCase):
    def test_plain_get_generates_portable_untrusted_manifest(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp)
            (root/'openapi.json').write_text(json.dumps({'openapi':'3.0.0','paths':{'/x':{'get':{'operationId':'x'}}}}))
            out = Path(rest_safe.generate(root, root/'out', 'demo', {})['output_path'])
            self.assertTrue((out/'plugin.json').is_file())
            self.assertFalse((out/'plugin.yaml').exists())
            manifest = json.loads((out/'plugin.json').read_text())
            self.assertEqual(next(iter(manifest['extensions']['com.nousresearch.hermes']['servers'].values()))['trust'], 'untrusted')

    def test_post_generates_portable_untrusted_manifest(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp)
            spec = {'openapi':'3.0.0','paths':{'/echo':{'post':{'operationId':'echo','requestBody':{'required':True,'content':{'application/json':{'schema':{'type':'object','properties':{'message':{'type':'string'}},'required':['message']}}}}}}}}
            (root/'openapi.json').write_text(json.dumps(spec))
            out = Path(rest_safe.generate(root, root/'out', 'demo', {})['output_path'])
            manifest = json.loads((out/'plugin.json').read_text())
            self.assertEqual(next(iter(manifest['extensions']['com.nousresearch.hermes']['servers'].values()))['trust'], 'untrusted')
            self.assertTrue((out/'server.py').is_file())

    def test_real_host_gate_loopback(self):
        host, host_python = host_test_config(windows_only=False)
        import subprocess, sys, threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        calls = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                calls.append(('GET', self.path, None)); self.send_response(200); self.end_headers(); self.wfile.write(b'{"get":true}')
            def do_POST(self):
                data = self.rfile.read(int(self.headers['Content-Length']))
                calls.append(('POST', self.path, json.loads(data))); self.send_response(200); self.end_headers(); self.wfile.write(data)
            def log_message(self, *args): pass
        server = HTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); src = root/'src'; src.mkdir()
            body = {'required':True,'content':{'application/json':{'schema':{'type':'object','properties':{'message':{'type':'string'}},'required':['message']}}}}
            (src/'openapi.json').write_text(json.dumps({'openapi':'3.0.0','servers':[{'url':'https://example.invalid/api/v3'}], 'components':{'requestBodies':{'Echo':body}}, 'paths':{'/echo':{'post':{'operationId':'echo','requestBody':{'$ref':'#/components/requestBodies/Echo'}},'get':{'operationId':'getEcho'}}, '/blocked':{'get':{'operationId':'blocked'}}, '/item/{id}':{'get':{'operationId':'item','parameters':[{'name':'id','in':'path','required':True,'schema':{'type':'string'}}]}}}}))
            out = Path(rest_safe.generate(src, root/'out', 'demo', {})['output_path'])
            data = root/'data'; data.mkdir()
            (data/'openapi-authorization.json').write_text(json.dumps({'base_url':'http://127.0.0.1:%s' % server.server_port, 'operations':['GET /echo','POST /echo']}))
            code = r'''
import os, sys
from pathlib import Path
from hermes_cli.agent_plugins import load_agent_plugin
from tools import mcp_tool, mcp_tool_discovery, mcp_tool_lifecycle
from tools.terminal_tool import set_approval_callback
from model_tools import handle_function_call
package = load_agent_plugin(Path(sys.argv[1]), Path(sys.argv[2]))
assert not package.diagnostics, package.diagnostics
name, config = next(iter(package.mcp_servers.items()))
assert config['trust'] == 'untrusted'
try:
    names = mcp_tool_discovery.register_mcp_servers({name:config})
    assert len(names) == 4, names
    assert all(x is False for hints in mcp_tool._tool_read_only_hints.values() for x in hints.values())
    post = next(x for x in names if x.endswith('_echo'))
    get = next(x for x in names if x.endswith('_getecho'))
    blocked = next(x for x in names if x.endswith('_blocked'))
    item = next(x for x in names if x.endswith('_item'))
    os.environ['HERMES_SINGLE_QUERY_SESSION']='1'
    assert 'not approve' in handle_function_call(post, {'body':{'message':'no'}}).lower()
    assert 'not approve' in handle_function_call(get, {}).lower()
    os.environ.pop('HERMES_SINGLE_QUERY_SESSION')
    os.environ['HERMES_INTERACTIVE']='1'
    seen=[]
    def simulated_deny(command, description, **kwargs):
        seen.append(command); return 'n'
    set_approval_callback(simulated_deny)
    assert 'not approve' in handle_function_call(post, {'body':{'message':'no'}}).lower()
    def simulated_approval(command, description, **kwargs):
        seen.append(command); return 'once'
    set_approval_callback(simulated_approval)
    if sys.argv[3] == 'deny':
        assert 'rejected' in handle_function_call(post, {'body':{'message':'no'}}).lower()
        assert 'rejected' in handle_function_call(get, {}).lower()
        print('HOST_POST_OK')
        sys.exit(0)
    assert 'rejected' in handle_function_call(post, {'body':{'message':42}}).lower()
    assert 'rejected' in handle_function_call(blocked, {}).lower()
    result = handle_function_call(post, {'body':{'message':'hello'}})
    assert 'hello' in result.lower(), result
    assert 'true' in handle_function_call(get, {}).lower()
    assert 'rejected' in handle_function_call(item, {'id':'..'}).lower()
    assert 'rejected' in handle_function_call(item, {'id':'a/b'}).lower()
    assert 'true' in handle_function_call(item, {'id':'abc-1'}).lower()
    assert len(seen) == 8, seen
    print('HOST_POST_OK')
finally:
    set_approval_callback(None); mcp_tool_lifecycle.shutdown_mcp_servers()
'''
            env = dict(os.environ, HERMES_HOME=str(root/'home'), TMPDIR=tmp, TMP=tmp, TEMP=tmp, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH='', G2H_ALLOWED_BASE_URL='http://127.0.0.1:%s' % server.server_port, G2H_APPROVED_OPERATIONS=json.dumps(['GET /echo','POST /echo']))
            env['PATH'] = str(host_python.parent) + os.pathsep + env.get('PATH','')
            for key in ('HERMES_SINGLE_QUERY_SESSION','HERMES_GATEWAY_SESSION','HERMES_SESSION_PLATFORM','HERMES_CRON_SESSION','HERMES_YOLO_MODE'): env.pop(key, None)

            for policy, mode in [(['GET /echo','POST /echo'], 'deny'), (['GET /api/v2/echo','POST /api/v2/echo'], 'deny'), (['GET /api/v3/echo','POST /api/v3/echo','GET /api/v3/item/{id}'], 'allow')]:
                (data/'openapi-authorization.json').write_text(json.dumps({'base_url':f'http://127.0.0.1:{server.server_port}', 'operations':policy}))
                proc = subprocess.run([str(host_python), '-c', code, str(out), str(root/'data'), mode], cwd=str(host), env=env, capture_output=True, text=True, timeout=90)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertIn('HOST_POST_OK', proc.stdout)
            self.assertEqual(calls, [('POST','/api/v3/echo',{'message':'hello'}), ('GET','/api/v3/echo',None), ('GET','/api/v3/item/abc-1',None)])
