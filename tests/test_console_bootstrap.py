import ast
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TEMPLATE = Path(__file__).resolve().parents[1] / 'generators' / 'deferred_worker.py.txt'

def bootstrap():
    tree = ast.parse(TEMPLATE.read_text(encoding='utf-8'))
    return next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'PACKAGE_BOOTSTRAP' for t in n.targets))

class ConsoleBootstrapTests(unittest.TestCase):
    def run_payload(self, package, digests, **extra):
        if 'entrypoint' in package:
            package.setdefault('identity', {'name':'g2h-bootstrap-fixture', 'version':'1.02'})
        if 'entrypoint' in package and 'requirements' in extra:
            extra.setdefault('startup_args', [])
            extra.setdefault('startup_digest', hashlib.sha256(json.dumps(extra['startup_args'], ensure_ascii=True, separators=(',', ':')).encode()).hexdigest())
        frame = {'jsonrpc':'2.0', 'method':'notifications/reviewed-source', 'params':{'package':package, 'digests':digests, **extra}}
        return subprocess.run([sys.executable, '-I', '-c', bootstrap()], input=json.dumps(frame)+'\n', text=True, capture_output=True, timeout=20)

    def test_duplicate_keys_are_rejected_before_source(self):
        source = b"print('SOURCE_MARKER')\ndef main(): pass\n"
        path = 'synthetic_fixture/__init__.py'
        package = {'module':'synthetic_fixture', 'entrypoint':'main', 'files':{path:base64.b64encode(source).decode()}}
        frame = json.dumps({'jsonrpc':'2.0', 'method':'notifications/reviewed-source', 'params':{'package':package, 'digests':{path:hashlib.sha256(source).hexdigest()}, 'requirements':[]}})
        for malformed in (frame.replace('"jsonrpc": "2.0"', '"jsonrpc": "2.0", "jsonrpc": "2.0"'), frame.replace('"entrypoint": "main"', '"entrypoint": "main", "entrypoint": "main"'), frame.replace('"digests": {', '"digests": {"duplicate": "a", "duplicate": "b", ')):
            result = subprocess.run([sys.executable, '-I', '-c', bootstrap()], input=malformed+'\n', text=True, capture_output=True, timeout=20)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('SOURCE_MARKER', result.stdout + result.stderr)

    def test_entrypoint_runs_once(self):
        source = b"print('IMPORTED')\ndef main(): print('CALLED')\n"
        package = {'module':'synthetic_fixture', 'entrypoint':'main', 'files':{'synthetic_fixture/__init__.py':base64.b64encode(source).decode()}}
        result = self.run_payload(package, {'synthetic_fixture/__init__.py':hashlib.sha256(source).hexdigest()}, requirements=[])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ['IMPORTED', 'CALLED'])

    def test_normalized_identity_exact_version_and_installed_collision(self):
        source = b"from importlib.metadata import version, metadata\nprint(version('G2H.BOOTSTRAP_fixture'), metadata('g2h-bootstrap-fixture')['Name'])\ndef main(): pass\n"
        path = 'synthetic_fixture/__init__.py'
        package = {'module':'synthetic_fixture', 'entrypoint':'main', 'files':{path:base64.b64encode(source).decode()}}
        digest = {path:hashlib.sha256(source).hexdigest()}
        result = self.run_payload(package, digest, requirements=[])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), '1.02 g2h-bootstrap-fixture')
        package['identity'] = {'name':'pip', 'version':'123'}
        result = self.run_payload(package, digest, requirements=[])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('conflicts with installed distribution', result.stderr)
        self.assertNotIn('1.02', result.stdout)

    def test_requirements_and_digest_fail_before_import(self):
        source = b"print('SOURCE_MARKER')\ndef main(): pass\n"
        path = 'synthetic_fixture/__init__.py'
        package = {'module':'synthetic_fixture', 'entrypoint':'main', 'files':{path:base64.b64encode(source).decode()}}
        digest = {path:hashlib.sha256(source).hexdigest()}
        for kwargs, hashes in [({'requirements':['g2h-impossible-distribution-xyz>=1']}, digest), ({'requirements':['pip==999999999']}, digest), ({'requirements':['bad @ url']}, digest), ({}, digest), ({'requirements':[]}, {path:'0'*64}), ({'requirements':[], 'unknown':1}, digest)]:
            with self.subTest(kwargs=kwargs, hashes=hashes):
                result = self.run_payload(package, hashes, **kwargs)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('SOURCE_MARKER', result.stdout + result.stderr)

if __name__ == '__main__': unittest.main()
