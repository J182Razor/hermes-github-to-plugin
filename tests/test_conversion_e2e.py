import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from generators import skill_bundle


class ConversionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ['TMPDIR'])
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.src = self.root / 'src'
        self.src.mkdir()
        self.out = self.root / 'out'

    def test_skill_bundle_copies_only_self_contained_files(self):
        skill = self.src / 'skills' / 'demo'
        (skill / 'references').mkdir(parents=True)
        (skill / 'SKILL.md').write_text('---\nname: demo\ndescription: demo\n---\n', encoding='utf-8')
        (skill / 'references' / 'guide.md').write_text('guide', encoding='utf-8')
        (self.src / '.env').write_text('PRIVATE_TEST_VALUE', encoding='utf-8')
        result = skill_bundle.generate(str(self.src), str(self.out), 'bundle', {})
        output = Path(result['output_path'])
        self.assertEqual((output / 'skills/demo/references/guide.md').read_text(), 'guide')
        self.assertFalse(any('PRIVATE_TEST_VALUE' in p.read_text(encoding='utf-8') for p in output.rglob('*') if p.is_file()))

    def test_openapi_generated_handler_calls_loopback(self):
        from generators import rest_safe as rest
        from http.server import BaseHTTPRequestHandler, HTTPServer
        import threading, runpy
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200); self.end_headers(); self.wfile.write(json.dumps({'path':self.path}).encode())
            def log_message(self, *args): pass
        server = HTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        (self.src/'openapi.json').write_text(json.dumps({'openapi':'3.0.0','paths':{'/items':{'get':{'operationId':'listItems'}}}}))
        out=Path(rest.generate(self.src,self.out,'demo',{})['output_path']); data=self.root/'data'; data.mkdir()
        (data/'openapi-authorization.json').write_text(json.dumps({'base_url':f'http://127.0.0.1:{server.server_port}','operations':['GET /items']}))
        with patch.dict(os.environ, {'PLUGIN_DATA':str(data)}): runtime=runpy.run_path(str(out/'server.py'))
        response=runtime['invoke'](runtime['OPERATIONS'][0], {})
        self.assertTrue(response['success'], response); self.assertEqual(response['data']['path'], '/items')

    def test_openapi_does_not_send_approved_requests_through_environment_proxy(self):
        from generators import rest_safe as rest
        from http.server import BaseHTTPRequestHandler, HTTPServer
        import threading, runpy
        calls=[]
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                calls.append(self.server.label); self.send_response(200); self.end_headers(); self.wfile.write(b'{}')
            def log_message(self, *args): pass
        servers=[HTTPServer(('127.0.0.1',0),Handler) for _ in range(2)]
        for server,label in zip(servers,('destination','proxy')):
            server.label=label; threading.Thread(target=server.serve_forever,daemon=True).start()
            self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        (self.src/'openapi.json').write_text(json.dumps({'openapi':'3.0.0','paths':{'/items':{'get':{'operationId':'listItems'}}}}))
        out=Path(rest.generate(self.src,self.out,'proxy-test',{})['output_path']); data=self.root/'data'; data.mkdir()
        (data/'openapi-authorization.json').write_text(json.dumps({'base_url':f'http://127.0.0.1:{servers[0].server_port}','operations':['GET /items']}))
        proxy=f'http://127.0.0.1:{servers[1].server_port}'
        with patch.dict(os.environ, {'PLUGIN_DATA':str(data),'http_proxy':proxy,'HTTP_PROXY':proxy,'no_proxy':'','NO_PROXY':''}):
            runtime=runpy.run_path(str(out/'server.py')); answer=runtime['invoke'](runtime['OPERATIONS'][0], {})
        self.assertTrue(answer['success'],answer); self.assertEqual(calls,['destination'])

    def test_mcp_rejects_environment_overrides_before_output(self):
        from generators import mcp_safe
        (self.src / 'server.py').write_text('print("fixture")', encoding='utf-8')
        (self.src / 'mcp.json').write_text(json.dumps({'mcpServers': {'fixture': {'command':'python', 'args':['server.py'], 'env':{'TOKEN':'${TOKEN}'}}}}))
        with self.assertRaises(ValueError):
            mcp_safe.generate(str(self.src), str(self.out), 'demo', {})
        self.assertFalse(self.out.exists())

    def test_mcp_rejects_literal_secrets_before_output(self):
        from generators import mcp_safe
        (self.src / 'server.py').write_text('pass')
        (self.src / 'mcp.json').write_text(json.dumps({'mcpServers': {'fixture': {'command':'python', 'args':['server.py'], 'env':{'TOKEN':'secret'}}}}))
        with self.assertRaises(ValueError):
            mcp_safe.generate(str(self.src), str(self.out), 'demo', {})
        self.assertFalse(self.out.exists())

    def test_skill_symlink_escape_rejected(self):
        skill = self.src / 'skills' / 'demo'
        skill.mkdir(parents=True)
        (skill / 'SKILL.md').write_text('---\nname: demo\ndescription: demo\n---\n')
        outside = self.root / 'outside'
        outside.write_text('private')
        try:
            (skill / 'escape').symlink_to(outside)
        except OSError:
            self.skipTest('symlink creation unavailable')
        with self.assertRaises(ValueError):
            skill_bundle.generate(str(self.src), str(self.out), 'demo', {})
        self.assertFalse(self.out.exists())

    def test_collision_preserves_existing_output(self):
        skill = self.src / 'skills' / 'demo'
        skill.mkdir(parents=True)
        (skill / 'SKILL.md').write_text('---\nname: demo\ndescription: demo\n---\n')
        target = self.out / 'demo'
        target.mkdir(parents=True)
        (target / 'marker').write_text('keep')
        with self.assertRaises(FileExistsError):
            skill_bundle.generate(str(self.src), str(self.out), 'demo', {})
        self.assertEqual((target / 'marker').read_text(), 'keep')

    def test_unknown_conversion_fails_without_validation(self):
        import tools
        report = {'type': 'unknown', 'repo_path': str(self.src), 'suggested_name': 'demo'}
        with patch.object(tools.detector, 'detect_repo', return_value=report), patch.object(tools.subprocess, 'run') as run:
            result = json.loads(tools.handle_convert_repo({'repo_url':'https://github.com/a/b','output_dir':str(self.out)}))
        self.assertFalse(result['success'])
        run.assert_not_called()
        self.assertFalse(self.out.exists())
