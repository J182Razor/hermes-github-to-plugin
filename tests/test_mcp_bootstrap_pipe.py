import ast
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest


class BootstrapPipeTests(unittest.TestCase):
    def test_bootstrap_keeps_source_off_command_line_and_preserves_next_frame(self):
        tree = ast.parse(Path('generators/deferred_worker.py.txt').read_text())
        bootstrap = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign) and getattr(n.targets[0], 'id', '') == 'BOOTSTRAP')
        source = b"import os, sys\nprint(os.read(0, 4).decode())\n"
        encoded = base64.b64encode(source).decode()
        message = {'jsonrpc': '2.0', 'method': 'notifications/reviewed-source', 'params': {'source': encoded, 'digest': hashlib.sha256(source).hexdigest()}}
        args = [sys.executable, '-I', '-c', bootstrap]
        self.assertNotIn(encoded, subprocess.list2cmdline(args))
        result = subprocess.run(args, input=(json.dumps(message) + '\nNEXT').encode(), capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout.strip(), b'NEXT')
        message['params']['digest'] = '0' * 64
        result = subprocess.run(args, input=(json.dumps(message) + '\nNEXT').encode(), capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b'')

    def test_oversized_frame_fails_closed(self):
        tree = ast.parse(Path('generators/deferred_worker.py.txt').read_text())
        bootstrap = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign) and getattr(n.targets[0], 'id', '') == 'BOOTSTRAP')
        result = subprocess.run([sys.executable, '-I', '-c', bootstrap], input=b'x' * 1048577 + b'\n', capture_output=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b'')


if __name__ == '__main__':
    unittest.main()
