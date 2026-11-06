import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

TEMPLATE = Path('generators/deferred_adapter.py.txt')

@unittest.skipUnless(os.name == 'nt', 'Windows adapter')
class StartupArgsTests(unittest.TestCase):
    def test_bounds_default_and_links(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as temp:
            root = Path(temp)
            source = root / 'source' / 'fixture' / '__init__.py'
            source.parent.mkdir(parents=True)
            source.write_bytes(b'fixture')
            consent = root / 'consent'
            consent.write_bytes(b'I TRUST THIS SOURCE WITH PERSISTENT FULL OS AUTHORITY')
            file = root / 'mcp-startup-args.json'
            sent = []
            class Worker:
                def __init__(self, command): pass
                def close(self): pass
                def request(self, payload, **kwargs):
                    sent.append(json.loads(payload))
                    return b'{"success":true}'
            ns = {'__file__':str(root/'adapter.py'), 'os':os, 'DIGEST':None, 'PACKAGE_MODULE':'fixture', 'PACKAGE_HASHES':{'fixture/__init__.py':hashlib.sha256(b'fixture').hexdigest()}, 'CONSOLE_FUNCTION':'main', 'CONSOLE_REQUIREMENTS':[], 'CONSOLE_IDENTITY':{'name':'fixture', 'version':'1'}, 'PersistentWorker':Worker}
            exec(TEMPLATE.read_text(), ns)
            call = {'toolName':'x', 'arguments':{}}
            with mock.patch.object(sys, 'argv', ['adapter', str(consent)]):
                manager = ns['SessionManager']()
                self.assertTrue(manager.handle(call)['success'])
                self.assertEqual(sent[-1]['startup_args'], [])
                old_worker = manager.worker
                file.write_text('["\\ud800"]', encoding='utf-8')
                self.assertIn('invalid startup arguments', manager.handle(call)['error'])
                self.assertIs(manager.worker, old_worker)
                self.assertEqual(len(sent), 1)
                manager.close()
                for content in ('{}', '[null]', json.dumps(['x']*65), json.dumps(['x'*4097]), '["\\u0000"]', ' '*16385, '{'):
                    file.write_text(content, encoding='utf-8')
                    before = len(sent)
                    self.assertFalse(manager.handle(call)['success'])
                    self.assertEqual(len(sent), before)
                file.unlink()
                os.link(consent, file)
                self.assertFalse(manager.handle(call)['success'])
                self.assertEqual(len(sent), 1)
                file.unlink()
                try:
                    file.symlink_to(consent)
                except OSError as exc:
                    self.skipTest('symlink creation unavailable: ' + str(exc))
                self.assertFalse(manager.handle(call)['success'])
                self.assertEqual(len(sent), 1)
