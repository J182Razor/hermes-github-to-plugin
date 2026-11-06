import ast
import asyncio
import base64
import hashlib
import io
import json
import re
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load_template(name):
    namespace = {'__name__': 'sdk_test'}
    exec(compile((ROOT / 'generators' / name).read_text(encoding='utf-8'), name, 'exec'), namespace)
    return namespace


def server_version_function():
    text = (ROOT / 'generators/deferred_server.py.txt').read_text(encoding='utf-8')
    tree = ast.parse(text)
    func = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'sdk_major')
    namespace = {'re': re}
    exec(compile(ast.Module(body=[func], type_ignores=[]), '<server>', 'exec'), namespace)
    return namespace['sdk_major']


class SDKCompatibilityTests(unittest.TestCase):
    def test_versions_fail_closed(self):
        worker = load_template('deferred_worker.py.txt')
        for check in (worker['sdk_major'], server_version_function()):
            for value in ('1.9.4', '2.0.0'):
                self.assertEqual(check(value), value[0])
            for value in ('1', '1.2', '1.2.3.4', '1.9.4garbage', '1.9.4rc1', '01.2.3', '1.02.3', '3.0.0', '', None):
                with self.subTest(value=value), self.assertRaises(RuntimeError):
                    check(value)

    def test_result_shape_fail_closed(self):
        convert = load_template('deferred_worker.py.txt')['tool_result']
        class Fake:
            def __init__(self, attr, value, wire):
                if attr: setattr(self, attr, value)
                self.wire = wire
            def model_dump(self, **kwargs):
                self.kwargs = kwargs
                return self.wire
        for major, attr in [('1', 'isError'), ('2', 'is_error')]:
            for flag in (False, True):
                obj = Fake(attr, flag, {'isError': flag, 'content': []})
                self.assertEqual(convert(obj, major)['success'], not flag)
                self.assertEqual(obj.kwargs, {'mode': 'json', 'by_alias': True, 'exclude_none': True})
            contradictory = Fake(attr, False, {'isError': False})
            setattr(contradictory, 'is_error' if major == '1' else 'isError', True)
            for obj in (contradictory, Fake(None, None, {'isError': False}), Fake(attr, 0, {'isError': False}),
                        Fake(attr, False, {}), Fake(attr, False, {'isError': True}),
                        Fake(attr, False, {'isError': False, 'is_error': True}), Fake(attr, False, [])):
                with self.subTest(major=major, wire=obj.wire), self.assertRaises(RuntimeError):
                    convert(obj, major)

    def test_real_installed_sdk_result_serialization(self):
        from importlib.metadata import version
        from mcp import types as mcp_types
        worker = load_template('deferred_worker.py.txt')
        major = worker['sdk_major'](version('mcp'))
        for flag in (False, True):
            obj = mcp_types.CallToolResult(content=[mcp_types.TextContent(type='text', text='actual')], isError=flag)
            result = worker['tool_result'](obj, major)
            self.assertIs(result['success'], not flag)
            self.assertIs(result['result']['isError'], flag)
            self.assertEqual(result['result']['content'][0]['text'], 'actual')

    def test_bad_versions_do_not_launch_worker_source(self):
        worker = load_template('deferred_worker.py.txt')
        data = b'print(123)'
        request = json.dumps({'source': base64.b64encode(data).decode(), 'digest': hashlib.sha256(data).hexdigest(), 'name': 'x', 'arguments': {}}).encode() + b'\n'
        for version in ('1.9.4bad', '3.0.0'):
            stdin = types.SimpleNamespace(buffer=io.BytesIO(request))
            with self.subTest(version=version), patch('sys.stdin', stdin), patch('importlib.metadata.version', return_value=version), patch.dict(worker, {'stdio_client': lambda *a, **k: (_ for _ in ()).throw(AssertionError('source launched'))}):
                with self.assertRaises(RuntimeError):
                    asyncio.run(worker['main']())


if __name__ == '__main__':
    unittest.main()
