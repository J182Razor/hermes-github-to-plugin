import os
from pathlib import Path
import tempfile
import unittest
from generators import mcp_safe

class ConsoleConversionTests(unittest.TestCase):
    def test_rejects_invalid_project_identity_and_dependency_collision(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); repo = root / 'repo'; package = repo / 'src' / 'examplepkg'; package.mkdir(parents=True)
            (package / '__init__.py').write_text('def main(): pass\n')
            suffix = 'dependencies=["mcp>=1"]\n[project.scripts]\nfixture="examplepkg:main"\n'
            variants = ['version="1"\n', 'name="fixture"\n', 'name="bad name"\nversion="1"\n',
                        'name="fixture"\nversion="1rc1"\n', 'name="fixture"\nversion="1"\ndynamic=["name"]\n',
                        'name="' + 'a'*129 + '"\nversion="1"\n', 'name="fixture"\nversion="' + '1'*65 + '"\n',
                        'name="MCP"\nversion="1"\n']
            for variant in variants:
                (repo / 'pyproject.toml').write_text('[project]\n' + variant + suffix)
                with self.subTest(variant=variant), self.assertRaises(ValueError):
                    mcp_safe.generate(repo, root / 'out', 'fixture', {'type':'mcp', '_explicit_mcp':True})

    def test_invalid_metadata_fails_closed(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); repo = root / 'repo'; package = repo / 'src' / 'examplepkg'; package.mkdir(parents=True)
            (package / '__init__.py').write_text('def main(): pass\n')
            base = '[project]\nname="fixture"\nversion="1"\n'
            variants = [
                'dependencies=[]\n[project.scripts]\nfixture="examplepkg:main"\n',
                'dependencies=["mcp @ https://example.invalid/mcp"]\n[project.scripts]\nfixture="examplepkg:main"\n',
                'dependencies=["mcp>=1"]\ndynamic=["version"]\n[project.scripts]\nfixture="examplepkg:main"\n',
                'dependencies=["mcp>=1"]\n[project.optional-dependencies]\nextra=[]\n[project.scripts]\nfixture="examplepkg:main"\n',
                'dependencies=["mcp>=1"]\n[project.scripts]\na="examplepkg:main"\nb="examplepkg:main"\n',
                'dependencies=["mcp>=1"]\n[project.scripts]\nfixture="examplepkg:absent"\n',
            ]
            for i, variant in enumerate(variants):
                (repo / 'pyproject.toml').write_text(base + variant)
                with self.subTest(i=i), self.assertRaises(ValueError):
                    mcp_safe.generate(repo, root / 'out', 'fixture', {'type':'mcp', '_explicit_mcp':True})
            (repo / 'pyproject.toml').write_text(base + 'dependencies=["mcp>=1"]\n[project.scripts]\nfixture="examplepkg:main"\n')
            with self.assertRaises(ValueError):
                mcp_safe.generate(repo, root / 'out', 'fixture', {'type':'cli'})

    def test_explicit_console_conversion_does_not_execute_source(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); src = root / 'repo'; package = src / 'src' / 'examplepkg'; package.mkdir(parents=True)
            marker = root / 'marker'
            (package / '__init__.py').write_text('from pathlib import Path\nPath(%r).touch()\ndef main(): pass\n' % str(marker))
            (src / 'pyproject.toml').write_text('[project]\nname="fixture"\nversion="1"\ndependencies=["mcp>=1"]\n[project.scripts]\nfixture-cli="examplepkg:main"\n')
            out = Path(mcp_safe.generate(src, root / 'out', 'fixture', {'type':'mcp', '_explicit_mcp':True})['output_path'])
            self.assertTrue((out / 'source/examplepkg/__init__.py').is_file())
            self.assertFalse(marker.exists())
            self.assertFalse((src / 'mcp.json').exists())
