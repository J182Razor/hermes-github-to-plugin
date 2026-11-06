from host_test_config import host_test_config
import json, os, tempfile, unittest
from pathlib import Path
from generators import rest_safe

class AuthGeneration(unittest.TestCase):
    def test_unused_schemes_are_ignored(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp)
            spec = {'openapi':'3.0.0', 'components':{'securitySchemes':{'a':{'type':'http','scheme':'bearer'}, 'unused':{'type':'oauth2','flows':{}}}}, 'security':[{'a':[]}], 'paths':{'/x':{'get':{'operationId':'x'}}}}
            (root/'openapi.json').write_text(json.dumps(spec))
            self.assertTrue(Path(rest_safe.generate(root, root/'out', 'demo', {})['output_path']).exists())

    def test_reachable_security_constraints(self):
        schemes = {'a':{'type':'http','scheme':'bearer'}, 'b':{'type':'apiKey','in':'header','name':'X-Key'}, 'oauth':{'type':'oauth2','flows':{}}}
        cases = [([{'oauth':[]}], [], True), ([{'a':[]}, {'b':[]}], [], True), ([{'a':[], 'b':[]}], [], True), ([{'a':[]}], [{'b':[]}], True), ([{'oauth':[]}], [], False)]
        for global_security, override, reject in cases:
            with self.subTest(global_security=global_security, override=override, reject=reject), tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
                root = Path(tmp)
                paths = {'/x':{'get':{'operationId':'x'}}}
                if override: paths['/y'] = {'get':{'operationId':'y','security':override}}
                if not reject: paths['/x']['get']['security'] = []
                spec = {'openapi':'3.0.0','components':{'securitySchemes':schemes},'security':global_security,'paths':paths}
                (root/'openapi.json').write_text(json.dumps(spec))
                if reject:
                    with self.assertRaises(ValueError): rest_safe.generate(root, root/'out', 'demo', {})
                    self.assertFalse((root/'out').exists())
                else:
                    out = Path(rest_safe.generate(root, root/'out', 'demo', {})['output_path'])
                    config = next(iter(json.loads((out/'mcp.json').read_text())['mcpServers'].values()))
                    self.assertNotIn('G2H_API_CREDENTIAL', config.get('env', {}))

    def test_bearer_get_is_portable_and_uses_placeholder(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp)
            spec = {'openapi':'3.0.0', 'components':{'securitySchemes':{'auth':{'type':'http','scheme':'bearer'}}}, 'security':[{'auth':[]}], 'paths':{'/x':{'get':{'operationId':'x'}}}}
            (root/'openapi.json').write_text(json.dumps(spec))
            out = Path(rest_safe.generate(root, root/'out', 'demo', {})['output_path'])
            config = next(iter(json.loads((out/'mcp.json').read_text())['mcpServers'].values()))
            self.assertEqual(config['env'], {'G2H_API_CREDENTIAL':'${G2H_API_CREDENTIAL}'})

    def test_unsupported_security_rejected_before_output(self):
        cases = [({'type':'apiKey','in':'query','name':'key'}, [{'a':[]}]),
                 ({'type':'oauth2','flows':{}}, [{'a':['scope']}]),
                 ({'type':'http','scheme':'bearer'}, [{'a':[]}, {}]),
                 ({'type':'http','scheme':'bearer'}, [{'missing':[]}])]
        for scheme, security in cases:
            with self.subTest(scheme=scheme, security=security), tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
                root = Path(tmp)
                spec = {'openapi':'3.0.0','components':{'securitySchemes':{'a':scheme}},'security':security,'paths':{'/x':{'get':{'operationId':'x'}}}}
                (root/'openapi.json').write_text(json.dumps(spec))
                with self.assertRaises(ValueError): rest_safe.generate(root, root/'out', 'demo', {})
                self.assertFalse((root/'out').exists())

    def test_real_dispatch_bearer_and_api_key(self):
        host, host_python = host_test_config(windows_only=False)
        import subprocess, sys, threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        sentinel = 'synthetic-test-credential-4782'
        calls = []
        class Handler(BaseHTTPRequestHandler):
            def dispatch(self):
                calls.append((self.command, self.path, self.headers.get('Authorization'), self.headers.get('X-Api-Key')))
                self.send_response(200); self.end_headers()
                self.wfile.write(json.dumps({'value': sentinel if self.path == '/echo' else 'safe'}).encode())
            do_GET = dispatch
            do_POST = dispatch
            def log_message(self, *args): pass
        server = HTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        code = r'''
import os, sys
from pathlib import Path
from unittest.mock import patch
from hermes_cli.agent_plugins import load_agent_plugin
from tools import mcp_tool_discovery, mcp_tool_lifecycle
from tools.terminal_tool import set_approval_callback
from model_tools import handle_function_call
sentinel = 'synthetic-test-credential-4782'
values = {'valid':sentinel, 'missing':'', 'unresolved':'${G2H_API_CREDENTIAL}', 'injection':'bad\r\nheader', 'oversize':'x'*4097}
with patch.dict(os.environ, {'G2H_API_CREDENTIAL':values[sys.argv[3]]}):
    package = load_agent_plugin(Path(sys.argv[1]), Path(sys.argv[2]))
    assert not package.diagnostics
    name, config = next(iter(package.mcp_servers.items()))
    assert config['trust'] == 'untrusted'
    try:
        names = mcp_tool_discovery.register_mcp_servers({name:config})
        get = next(n for n in names if n.endswith('_get'))
        post = next(n for n in names if n.endswith('_post'))
        echo = next(n for n in names if n.endswith('_echo'))
        public = next(n for n in names if n.endswith('_public'))
        if sys.argv[3] != 'valid':
            os.environ['HERMES_INTERACTIVE']='1'
            set_approval_callback(lambda *a, **k: 'once')
            assert 'failed' in handle_function_call(get, {}).lower()
            sys.exit(0)
        os.environ['HERMES_SINGLE_QUERY_SESSION']='1'
        assert 'not approve' in handle_function_call(get, {}).lower()
        os.environ.pop('HERMES_SINGLE_QUERY_SESSION')
        os.environ['HERMES_INTERACTIVE']='1'
        set_approval_callback(lambda *a, **k: 'n')
        assert 'not approve' in handle_function_call(post, {'body':{}}).lower()
        set_approval_callback(lambda *a, **k: 'once')
        assert 'safe' in handle_function_call(get, {})
        assert 'safe' in handle_function_call(post, {'body':{}})
        result = handle_function_call(echo, {})
        assert sentinel not in result and 'failed' in result
        assert 'safe' in handle_function_call(public, {})
    finally:
        set_approval_callback(None); mcp_tool_lifecycle.shutdown_mcp_servers()
'''
        for scheme in ({'type':'http','scheme':'bearer'}, {'type':'apiKey','in':'header','name':'X-Api-Key'}):
            with self.subTest(kind=scheme['type']), tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
                root = Path(tmp); src = root/'src'; src.mkdir(); data = root/'data'; data.mkdir()
                body = {'required':True,'content':{'application/json':{'schema':{'type':'object','properties':{}}}}}
                spec = {'openapi':'3.0.0','components':{'securitySchemes':{'a':scheme, 'unused':{'type':'oauth2','flows':{}}}},'security':[{'a':[]}], 'paths':{'/get':{'get':{'operationId':'get'}},'/post':{'post':{'operationId':'post','requestBody':body}},'/echo':{'get':{'operationId':'echo'}},'/public':{'get':{'operationId':'public','security':[]}}}}
                (src/'openapi.json').write_text(json.dumps(spec))
                out = Path(rest_safe.generate(src, root/'out', 'demo', {})['output_path'])
                (data/'openapi-authorization.json').write_text(json.dumps({'base_url':'http://127.0.0.1:%s' % server.server_port,'operations':['GET /get','POST /post','GET /echo','GET /public']}))
                env = dict(os.environ, HERMES_HOME=str(root/'home'), TMPDIR=tmp, TMP=tmp, TEMP=tmp, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH='')
                env['PATH'] = str(host_python.parent) + os.pathsep + env.get('PATH','')
                for key in ('G2H_API_CREDENTIAL','HERMES_SINGLE_QUERY_SESSION','HERMES_GATEWAY_SESSION','HERMES_SESSION_PLATFORM','HERMES_CRON_SESSION','HERMES_YOLO_MODE'): env.pop(key, None)

                for mode in ('missing', 'unresolved', 'injection', 'oversize', 'valid'):
                    count = len(calls)
                    proc = subprocess.run([str(host_python), '-c', code, str(out), str(data), mode], cwd=str(host), env=env, capture_output=True, text=True, timeout=90)
                    self.assertNotIn(sentinel, proc.stdout + proc.stderr)
                    self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                    if mode != 'valid': self.assertEqual(len(calls), count)
                recent = calls[-4:]
                self.assertEqual([x[0:2] for x in recent], [('GET','/get'),('POST','/post'),('GET','/echo'),('GET','/public')])
                expected = ('Bearer ' + sentinel, None) if scheme['type'] == 'http' else (None, sentinel)
                self.assertTrue(all(x[2:] == expected for x in recent[:3]))
                self.assertEqual(recent[3][2:], (None, None))
