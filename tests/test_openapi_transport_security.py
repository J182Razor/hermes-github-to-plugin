import json, os, runpy, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from generators import rest_safe

class TransportSecurity(unittest.TestCase):
    def test_generated_runtime_rejects_remote_cleartext_before_request(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); src = root / 'src'; src.mkdir(); data = root / 'data'; data.mkdir()
            spec = {'openapi':'3.0.0','components':{'securitySchemes':{'a':{'type':'http','scheme':'bearer'}}},'security':[{'a':[]}], 'paths':{'/x':{'get':{'operationId':'x'}}}}
            (src/'openapi.json').write_text(json.dumps(spec))
            out = Path(rest_safe.generate(src, root/'out', 'demo', {})['output_path'])
            (data/'openapi-authorization.json').write_text(json.dumps({'base_url':'http://example.invalid','operations':['GET /x']}))
            with patch.dict(os.environ, {'PLUGIN_DATA':str(data), 'G2H_API_CREDENTIAL':'synthetic-secret'}):
                server = runpy.run_path(str(out/'server.py'))
                with patch.dict(server['invoke'].__globals__, {'Request': unittest.mock.Mock(side_effect=AssertionError('request sink reached'))}):
                    sink = server['invoke'].__globals__['Request']
                    result = server['invoke'](server['OPERATIONS'][0], {})
                    self.assertFalse(result['success']); sink.assert_not_called()
                    self.assertNotIn('synthetic-secret', str(result))

    def test_credential_in_arguments_rejected_before_network(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); src = root/'src'; src.mkdir(); data = root/'data'; data.mkdir()
            spec = {'openapi':'3.0.0','components':{'securitySchemes':{'a':{'type':'http','scheme':'bearer'}}},'security':[{'a':[]}], 'paths':{'/x':{'get':{'operationId':'x','parameters':[{'name':'q','in':'query','schema':{'type':'string'}}]}}}}
            (src/'openapi.json').write_text(json.dumps(spec))
            out = Path(rest_safe.generate(src, root/'out', 'demo', {})['output_path'])
            (data/'openapi-authorization.json').write_text(json.dumps({'base_url':'https://example.invalid','operations':['GET /x']}))
            with patch.dict(os.environ, {'PLUGIN_DATA':str(data), 'G2H_API_CREDENTIAL':'synthetic-secret'}):
                server = runpy.run_path(str(out/'server.py'))
                with patch.dict(server['invoke'].__globals__, {'Request': unittest.mock.Mock(side_effect=AssertionError('sink'))}):
                    sink = server['invoke'].__globals__['Request']
                    self.assertFalse(server['invoke'](server['OPERATIONS'][0], {'q':'synthetic-secret'})['success'])
                    sink.assert_not_called()

    def test_generated_runtime_transport_matrix(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); src = root/'src'; src.mkdir(); data = root/'data'; data.mkdir()
            spec = {'openapi':'3.0.0','components':{'securitySchemes':{'a':{'type':'http','scheme':'bearer'}}},'security':[{'a':[]}], 'paths':{'/x':{'get':{'operationId':'x'}}}}
            (src/'openapi.json').write_text(json.dumps(spec))
            out = Path(rest_safe.generate(src, root/'out', 'demo', {})['output_path'])
            (data/'openapi-authorization.json').write_text(json.dumps({'base_url':'https://example.invalid','operations':['GET /x']}))
            with patch.dict(os.environ, {'PLUGIN_DATA':str(data), 'G2H_API_CREDENTIAL':'synthetic-secret'}):
                server = runpy.run_path(str(out/'server.py')); g = server['invoke'].__globals__
                class Response:
                    status = 200
                    def __enter__(self): return self
                    def __exit__(self, *args): pass
                    def read(self, n): return b'"ok"'
                class Opener:
                    def open(self, *args, **kwargs): return Response()
                with patch.dict(g, {'build_opener':lambda *a: Opener()}):
                    for base, allowed in [('http://127.0.0.1',True),('http://127.1.2.3',True),('http://[::1]',True),('https://example.invalid',True),('http://localhost',False),('http://127.1',False),('http://2130706433',False),('http://[::ffff:127.0.0.1]',False),('http://@127.0.0.1',False),('http://example.invalid',False)]:
                        with self.subTest(base=base):
                            g['BASE'] = base
                            self.assertEqual(server['invoke'](server['OPERATIONS'][0], {})['success'], allowed)
