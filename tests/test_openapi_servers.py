import json
import os
import runpy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from generators import rest_safe


class RootServers(unittest.TestCase):
    def generate(self, root, servers=None, paths=None):
        spec = {'openapi': '3.0.0', 'paths': paths or {'/pet': {'get': {'operationId': 'getPet'}}}}
        if servers is not None:
            spec['servers'] = servers
        (root / 'openapi.json').write_text(json.dumps(spec))
        return Path(rest_safe.generate(root, root / 'out', 'demo', {})['output_path'])

    def test_no_implicit_authorization_and_static_path(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp)
            out = self.generate(root, [{'url': 'https://example.invalid/api/v3'}])
            with patch.dict(os.environ, {'PLUGIN_DATA': str(root)}, clear=False):
                runtime = runpy.run_path(str(out / 'server.py'))
            self.assertEqual(runtime['SERVER_PATH'], '/api/v3')
            self.assertFalse(runtime['invoke'](runtime['OPERATIONS'][0], {})['success'])
            self.assertIn('not automatically authorized', (out / 'README.md').read_text())

    def test_no_server_retains_origin_only_behavior(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp)
            out = self.generate(root)
            with patch.dict(os.environ, {'PLUGIN_DATA': str(root)}, clear=False):
                runtime = runpy.run_path(str(out / 'server.py'))
            self.assertEqual(runtime['SERVER_PATH'], '')

    def test_unsafe_servers_rejected_before_output(self):
        invalid = [[], [{'url': 'https://a.invalid'}, {'url': 'https://b.invalid'}],
                   [{'url': 'http://a.invalid'}], [{'url': 'https://user@a.invalid/api'}],
                   [{'url': 'https://a.invalid/api?'}], [{'url': 'https://a.invalid/api#'}],
                   [{'url': 'https://a.invalid/{version}'}], [{'url': 'https://a.invalid/%2e%2e'}],
                   [{'url': 'https://a.invalid/%252f'}], [{'url': 'https://a.invalid/a/../b'}],
                   [{'url': 'https://a.invalid/a/./b'}], [{'url': 'https://a.invalid/a//b'}],
                   [{'url': 'https://a.invalid/api/'}], [{'url': 'https://a.invalid/'}],
                   [{'url': 'https://a.invalid/a\\b'}], [{'url': 'https://a.invalid', 'variables': {}}],
                   [{'url': 'https://a.invalid:99999'}], [{'url': 'https://.../api'}],
                   [{'url': 'https://-bad..host/api'}], [{'url': 'https://bad-.host/api'}],
                   [{'url': 'https://256.1.1.1/api'}], [{'url': 'https://xn--/api'}],
                   [{'url': 'https://' + 'a'*64 + '.invalid/api'}], [{'url': 'https://a.invalid:0/api'}],
                   [{'url': 'https://a.invalid\n/api'}], [{'url': 'https://[::1%25lo]/api'}]]
        for servers in invalid:
            with self.subTest(servers=servers), tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
                root = Path(tmp)
                with patch.object(Path, 'mkdir', side_effect=AssertionError('output sink reached')):
                    with self.assertRaises(ValueError):
                        self.generate(root, servers)

    def test_valid_declared_hosts(self):
        for url in ('https://example.invalid/api', 'https://xn--bcher-kva.example:443/api', 'https://127.0.0.1/api', 'https://[::1]:443/api'):
            with self.subTest(url=url), tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
                self.assertTrue((self.generate(Path(tmp), [{'url': url}]) / 'server.py').is_file())

    def test_operation_and_path_server_overrides_rejected(self):
        for paths in ({'/pet': {'get': {'operationId': 'x', 'servers': []}}},
                      {'/pet': {'servers': [{'url': 'https://b.invalid'}], 'get': {'operationId': 'x'}}}):
            with self.subTest(paths=paths), tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
                root = Path(tmp)
                with patch.object(Path, 'mkdir', side_effect=AssertionError('output sink reached')):
                    with self.assertRaises(ValueError):
                        self.generate(root, [{'url': 'https://a.invalid/api'}], paths)

    def test_colliding_operation_names_still_rejected(self):
        paths = {'/a': {'get': {'operationId': 'Foo'}}, '/b': {'get': {'operationId': 'foo'}}}
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            with self.assertRaisesRegex(ValueError, 'collision'):
                self.generate(Path(tmp), [{'url': 'https://a.invalid/api'}], paths)
