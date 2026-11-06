import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import tools


def fixture(tmp_path, deps='"mcp>=1"', scripts='mcp_fixture="examplepkg:main"', readme='MCP server'):
    repo = tmp_path / 'repo'
    package = repo / 'src' / 'examplepkg'
    package.mkdir(parents=True)
    marker = tmp_path / 'executed'
    (package / '__init__.py').write_text('from pathlib import Path\nPath(%r).touch()\nfrom mcp.server.fastmcp import FastMCP\nmcp = FastMCP("fixture")\ndef main(): mcp.run()\n' % str(marker))
    (repo / 'pyproject.toml').write_text('[project]\nname="fixture"\nversion="1"\ndependencies=[' + deps + ']\n[project.scripts]\n' + scripts + '\n')
    (repo / 'README.md').write_text(readme)
    return repo, marker


def convert(repo, out, forced=''):
    report = tools.detector.detect_type(str(repo))
    report['repo_path'] = str(repo)
    return report, json.loads(tools._convert_detected(report, forced, 'fixture', str(out)))


class AutoMcpRegressionTests(unittest.TestCase):
    def test_auto_console_no_source_execution(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as directory:
            tmp_path = Path(directory)
            repo, marker = fixture(tmp_path)
            report, result = convert(repo, tmp_path / 'out')
            assert report['type'] == 'mcp' and result['success'] and result['type'] == 'mcp'
            assert (Path(result['output_path']) / 'source/examplepkg/__init__.py').is_file()
            assert not marker.exists()

    def test_readme_only_false_positive_fails_closed(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as directory:
            tmp_path = Path(directory)
            repo, marker = fixture(tmp_path, deps='', scripts='tool="examplepkg:main"')
            (repo / 'server.py').write_text('# unrelated server filename\n')
            (repo / 'mcp_server.py').write_text('# unrelated filename\n')
            (repo / 'src' / 'mcp.py').write_text('# unrelated filename\n')
            report, result = convert(repo, tmp_path / 'out')
            assert report['type'] in ('mcp', 'unknown') and not result['success']
            assert 'ambiguous' in result['error'] and not marker.exists()

    def test_multiple_scripts_fail_closed(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as directory:
            tmp_path = Path(directory)
            repo, marker = fixture(tmp_path, scripts='mcp_a="examplepkg:main"\nmcp_b="examplepkg:main"')
            report, result = convert(repo, tmp_path / 'out')
            assert not result['success'] and not marker.exists()

    def test_explicit_cli_is_not_mcp(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as directory:
            tmp_path = Path(directory)
            repo, marker = fixture(tmp_path)
            _, result = convert(repo, tmp_path / 'out', 'cli')
            assert not result.get('type') == 'mcp'
            assert not marker.exists()

    def test_cli_detected_mcp_dependency_does_not_generate_cli(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as directory:
            tmp_path = Path(directory)
            repo, marker = fixture(tmp_path, scripts='tool="examplepkg:main"', readme='')
            (repo / 'bin').mkdir()
            report, result = convert(repo, tmp_path / 'out')
            assert report['type'] == 'cli' and not result['success']
            assert 'ambiguous' in result['error'] and not marker.exists()

    def test_auto_rejects_ambiguous_run_and_bindings(self):
        for prefix,run in [
            ('', 'mcp.run(transport="sse")'),
            ('', 'mcp.run(transport="streamable-http")'),
            ('', 'mcp.run("stdio")'),
            ('', 'mcp.run(transport="stdio", extra=True)'),
            ('', 'mcp.run(**{})'),
            ('try:\n raise ValueError()\nexcept ValueError as mcp:\n pass\n', 'mcp.run()'),
            ('from builtins import ValueError as Error\ntry:\n raise Error()\nexcept Error as FastMCP:\n pass\n', 'mcp.run()'),
            ('match 1:\n case mcp: pass\n', 'mcp.run()'),
            ('match [1]:\n case [*mcp]: pass\n', 'mcp.run()'),
            ('match {}:\n case {**mcp}: pass\n', 'mcp.run()'),
            ('def other():\n global mcp\n', 'mcp.run()'),
            ('def outer():\n mcp = 1\n def inner():\n  nonlocal mcp\n', 'mcp.run()'),
            ('raise SystemExit()\n', 'mcp.run()'),
        ]:
            with self.subTest(prefix=prefix, run=run):
                with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as directory:
                    tmp_path = Path(directory)
                    repo, marker = fixture(tmp_path)
                    source = ('from pathlib import Path\nPath(%r).touch()\n' % str(marker)
                              + 'from mcp.server.fastmcp import FastMCP\n'
                              + prefix + 'mcp = FastMCP("fixture")\ndef main(): ' + run + '\n')
                    (repo / 'src/examplepkg/__init__.py').write_text(source)
                    report, result = convert(repo, tmp_path / 'out')
                    assert report['type'] == 'unknown'
                    assert not result['success'] and 'ambiguous' in result['error']
                    assert not marker.exists()

    def test_public_handlers_reject_unsafe_auto_sources(self):
        for unsafe in [
            'def main(): mcp.run(transport="sse")\n',
            'try:\n raise ValueError()\nexcept ValueError as mcp:\n pass\ndef main(): mcp.run()\n',
        ]:
            with self.subTest(unsafe=unsafe):
                with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as directory:
                    tmp_path = Path(directory)
                    repo, marker = fixture(tmp_path)
                    path = repo / 'src/examplepkg/__init__.py'
                    path.write_text(path.read_text().replace('def main(): mcp.run()\n', unsafe))

                    def detect_repo(url, retain_clone=False):
                        report = tools.detector.detect_type(str(repo))
                        report['repo_path'] = str(repo)
                        return report

                    with patch.object(tools.detector, 'detect_repo', detect_repo):
                        url = 'https://github.com/example/fixture'
                        detected = json.loads(tools.handle_detect_repo({'repo_url': url}))
                        result = json.loads(tools.handle_convert_repo({
                            'repo_url': url, 'name': 'fixture', 'output_dir': str(tmp_path / 'out')}))
                        assert detected['success'] and detected['type'] == 'unknown'
                        assert not result['success'] and 'ambiguous' in result['error']
                        assert not marker.exists()

    def test_exception_binder_after_constructor_rejected(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as directory:
            tmp_path = Path(directory)
            repo, marker = fixture(tmp_path)
            path = repo / 'src/examplepkg/__init__.py'
            path.write_text(path.read_text().replace(
                'def main():', 'try:\n raise ValueError()\nexcept ValueError as mcp:\n pass\ndef main():'))
            report, result = convert(repo, tmp_path / 'out')
            assert report['type'] == 'unknown' and not result['success']
            assert 'ambiguous' in result['error'] and not marker.exists()

    def test_auto_accepts_stdio_run(self):
        for run in ['mcp.run()', 'mcp.run(transport="stdio")']:
            with self.subTest(run=run):
                with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as directory:
                    tmp_path = Path(directory)
                    repo, marker = fixture(tmp_path)
                    path = repo / 'src/examplepkg/__init__.py'
                    path.write_text(path.read_text().replace('mcp.run()', run))
                    report, result = convert(repo, tmp_path / 'out')
                    assert report['type'] == 'mcp' and result['success']
                    assert not marker.exists()

    def test_reexport_rejects_unconditional_raise(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as directory:
            tmp_path = Path(directory)
            repo, marker = fixture(tmp_path)
            pkg = repo / 'src/examplepkg'
            (pkg / 'server.py').write_text('from mcp.server.fastmcp import FastMCP\nmcp = FastMCP("fixture")\ndef main(): mcp.run()\n')
            (pkg / '__init__.py').write_text('from pathlib import Path\nPath(%r).touch()\nraise SystemExit()\nfrom .server import main\n' % str(marker))
            report, result = convert(repo, tmp_path / 'out')
            assert report['type'] == 'unknown'
            assert not result['success'] and 'ambiguous' in result['error']
            assert not marker.exists()
