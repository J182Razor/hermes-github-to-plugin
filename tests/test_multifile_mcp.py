import json
import os
from pathlib import Path
import tempfile
import unittest

from generators import mcp_safe


class MultiFileMCPTests(unittest.TestCase):
    def test_rejects_unsafe_packages_before_output(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp)
            repo = root / 'repo'
            package = repo / 'example'
            package.mkdir(parents=True)
            (package / '__init__.py').write_text('', encoding='utf-8')
            server = package / 'server.py'
            server.write_text('VALUE = 1', encoding='utf-8')
            config = repo / 'mcp.json'
            def set_module(module):
                config.write_text(json.dumps({'mcpServers': {'example': {'command': 'python', 'args': ['-m', module]}}}), encoding='utf-8')
            def rejected():
                with self.assertRaises(ValueError):
                    mcp_safe.generate(repo, root / 'out', 'example', {})
                self.assertFalse((root / 'out' / 'example').exists())
            for module in ('../example.server', 'example..server', 'example.class'):
                set_module(module)
                rejected()
            set_module('example.server')
            resource = package / 'data.json'
            resource.write_text('{}', encoding='utf-8')
            rejected()
            resource.unlink()
            server.write_text('import importlib\nimportlib.import_module("example.helper")', encoding='utf-8')
            rejected()
            server.write_text('from ..escape import x', encoding='utf-8')
            rejected()
            server.write_text('VALUE = 1', encoding='utf-8')
            extra = []
            for i in range(63):
                path = package / ('module%d.py' % i)
                path.write_text('', encoding='utf-8')
                extra.append(path)
            rejected()
            for path in extra:
                path.unlink()
            server.write_bytes(b'x' * 1048577)
            rejected()
            server.write_text('VALUE = 1', encoding='utf-8')
            try:
                (package / 'link.py').symlink_to(server)
            except OSError:
                pass
            else:
                rejected()
                (package / 'link.py').unlink()
            result = mcp_safe.generate(repo, root / 'out', 'example', {})
            self.assertTrue(Path(result['output_path']).is_dir())

    def test_missing_local_symbol_rejected_and_constant_accepted(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp)
            repo = root / 'repo'
            package = repo / 'example'
            package.mkdir(parents=True)
            (package / '__init__.py').write_text('', encoding='utf-8')
            (package / 'helper.py').write_text('VALUE = 42\n', encoding='utf-8')
            server = package / 'server.py'
            (repo / 'mcp.json').write_text(json.dumps({'mcpServers': {'example': {'command': 'python', 'args': ['-m', 'example.server']}}}), encoding='utf-8')
            server.write_text('from .helper import missing\n', encoding='utf-8')
            with self.assertRaises(ValueError):
                mcp_safe.generate(repo, root / 'out', 'example', {})
            self.assertFalse((root / 'out' / 'example').exists())
            server.write_text('from .helper import VALUE\n', encoding='utf-8')
            self.assertTrue(Path(mcp_safe.generate(repo, root / 'out', 'example', {})['output_path']).is_dir())

    def test_module_package_is_copied_without_execution(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp)
            repo = root / 'repo'
            package = repo / 'example'
            package.mkdir(parents=True)
            marker = root / 'executed'
            (package / '__init__.py').write_text('', encoding='utf-8')
            (package / 'helper.py').write_text('VALUE = 42\n', encoding='utf-8')
            (package / 'server.py').write_text(
                'from .helper import VALUE\nfrom pathlib import Path\nPath('
                + repr(str(marker)) + ').touch()\n', encoding='utf-8')
            (repo / 'mcp.json').write_text(json.dumps({'mcpServers': {
                'example': {'command': 'python', 'args': ['-m', 'example.server']}
            }}), encoding='utf-8')
            output = Path(mcp_safe.generate(repo, root / 'out', 'example', {})['output_path'])
            self.assertFalse(marker.exists())
            copied = {p.read_bytes() for p in output.rglob('*.py') if p.is_file()}
            self.assertIn((package / 'helper.py').read_bytes(), copied)
            self.assertIn((package / 'server.py').read_bytes(), copied)
