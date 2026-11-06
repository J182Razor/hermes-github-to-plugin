import asyncio
import base64
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest.mock import patch


class CommandLimitTests(unittest.TestCase):
    def test_single_file_source_is_not_exposed_in_spawn_arguments(self):
        called = []
        class SinkReached(Exception):
            pass
        def sink(params, **kwargs):
            called.append(params)
            raise SinkReached
        class Params:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)
        mcp = types.ModuleType('mcp')
        mcp.ClientSession = object
        client = types.ModuleType('mcp.client')
        stdio = types.ModuleType('mcp.client.stdio')
        stdio.stdio_client = sink
        stdio.StdioServerParameters = Params
        with patch.dict(sys.modules, {'mcp': mcp, 'mcp.client': client, 'mcp.client.stdio': stdio}):
            namespace = {'__name__': 'worker_test'}
            exec(compile(Path('generators/package_envelope.py').read_bytes() + b'\n' + Path('generators/deferred_worker.py.txt').read_bytes(), 'worker', 'exec'), namespace)
        raw = b'SYNTHETIC_CANARY_SOURCE_87329 = 1\n'
        encoded = base64.b64encode(raw).decode()
        request = {'source': encoded, 'digest': hashlib.sha256(raw).hexdigest()}
        class Input:
            buffer = io.BytesIO((json.dumps(request) + '\n').encode())
        with patch.object(sys, 'stdin', Input()):
            with self.assertRaises(SinkReached):
                asyncio.run(namespace['main']())
        self.assertNotIn(encoded, called[0].args, 'source bytes exposed in process arguments')

    def test_complete_windows_command_line_boundary_before_spawn(self):
        called = []
        class SinkReached(Exception):
            pass
        def sink(params, **kwargs):
            called.append(params)
            raise SinkReached
        class Params:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)
        mcp = types.ModuleType('mcp')
        mcp.ClientSession = object
        client = types.ModuleType('mcp.client')
        stdio = types.ModuleType('mcp.client.stdio')
        stdio.stdio_client = sink
        stdio.StdioServerParameters = Params
        with patch.dict(sys.modules, {'mcp': mcp, 'mcp.client': client, 'mcp.client.stdio': stdio}):
            namespace = {'__name__': 'worker_test'}
            exec(compile(Path('generators/package_envelope.py').read_bytes() + b'\n' + Path('generators/deferred_worker.py.txt').read_bytes(), 'worker', 'exec'), namespace)
        raw = b'VALUE = 1\n'
        package = {'module': 'demo.server', 'files': {'demo/server.py': base64.b64encode(raw).decode()}}
        request = {'package': package, 'digests': {'demo/server.py': hashlib.sha256(raw).hexdigest()}}
        envelope = base64.b64encode(json.dumps(package, separators=(',', ':')).encode()).decode()
        def size(path):
            command = subprocess.list2cmdline([path, '-I', '-c', namespace['PACKAGE_BOOTSTRAP']])
            return len(command.encode('utf-16-le')) // 2 + 1
        prefix = 'C:\\long path\\😀'
        path = prefix + 'x' * (32767 - size(prefix))
        self.assertEqual(size(path), 32767)
        class Input:
            buffer = io.BytesIO((json.dumps(request) + '\n').encode())
        with patch.object(sys, 'stdin', Input()), patch.object(sys, 'executable', path):
            with self.assertRaises(SinkReached):
                asyncio.run(namespace['main']())
        self.assertEqual(len(called), 1)
        Input.buffer = io.BytesIO((json.dumps(request) + '\n').encode())
        with patch.object(sys, 'stdin', Input()), patch.object(sys, 'executable', path + 'x'):
            with self.assertRaisesRegex(ValueError, 'command.line'):
                asyncio.run(namespace['main']())
        self.assertEqual(len(called), 1)


if __name__ == '__main__':
    unittest.main()
