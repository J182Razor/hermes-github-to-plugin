import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import tools


def make_repo(tmp_path, source):
    repo = tmp_path / 'repo'
    pkg = repo / 'src' / 'mcp_client'
    pkg.mkdir(parents=True)
    (pkg / '__init__.py').write_text(source)
    (repo / 'README.md').write_text('MCP server')
    (repo / 'pyproject.toml').write_text('[project]\nname="fixture"\nversion="1"\ndependencies=["mcp>=1"]\n[project.scripts]\nmcp_client="mcp_client:main"\n')
    return repo


def convert(repo, out, forced='', name='fixture'):
    report = tools.detector.detect_type(str(repo))
    report['repo_path'] = str(repo)
    return json.loads(tools._convert_detected(report, forced, name, str(out)))


class AutoMcpRegressionTests(unittest.TestCase):
    def test_auto_rejects_unverified_server(self):
        for source in [
            'def main(): print("client")\n',
            'from mcp.server.fastmcp import FastMCP\ndef main(): print("client")\n',
            'class FastMCP:\n def run(self): pass\nmcp = FastMCP()\ndef main(): mcp.run()\n',
            'from mcp.server.fastmcp import FastMCP\nmcp = FastMCP("x")\ndef main(): pass\n',
            'from mcp.server.fastmcp import FastMCP\nmcp = FastMCP("x")\nmcp = object()\ndef main(): mcp.run()\n',
            'from mcp.server.fastmcp import FastMCP\nmcp = FastMCP("x")\nother = mcp\ndef main(): mcp.run()\n',
            'from mcp.server.fastmcp import FastMCP as Server\nmcp = Server("x")\ndef main(): mcp.run()\n',
            'from mcp.server.fastmcp import FastMCP\nmcp = FastMCP("x")\ndef main():\n return\n mcp.run()\n',
        ]:
            with self.subTest(source=source):
                with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as directory:
                    tmp_path = Path(directory)
                    repo = make_repo(tmp_path, source)
                    assert tools.detector.detect_type(str(repo))['type'] == 'unknown'
                    result = convert(repo, tmp_path / 'out')
                    assert not result['success'] and 'ambiguous' in result['error']
                    assert convert(repo, tmp_path / 'explicit', 'mcp')['success']

    def test_name_bound_and_clear_error(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as directory:
            tmp_path = Path(directory)
            assert len(tools.detector.default_plugin_name('https://github.com/owner/' + 'x'*100)) <= 64
            result = json.loads(tools.handle_convert_repo({'repo_url': 'https://github.com/a/b', 'name': 'x'*65}))
            assert not result['success'] and 'name' in result['error'] and '64' in result['error']

    def test_direct_one_hop_reexport(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as directory:
            tmp_path = Path(directory)
            repo = make_repo(tmp_path, 'from .server import main\n')
            (repo / 'src/mcp_client/server.py').write_text('from mcp.server.fastmcp import FastMCP\nmcp = FastMCP("x")\ndef main(): mcp.run()\n')
            assert convert(repo, tmp_path / 'out')['success']

    def test_dotted_fastmcp_import(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as directory:
            tmp_path = Path(directory)
            repo = make_repo(tmp_path, 'import mcp.server.fastmcp\nserver = mcp.server.fastmcp.FastMCP("x")\ndef main(): server.run()\n')
            assert convert(repo, tmp_path / 'out')['success']
