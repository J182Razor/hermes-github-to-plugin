import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from importlib.metadata import version

from generators import fixed_adapter


class FixedDependencies(unittest.TestCase):
    def test_requirements_before_execution(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); repo = root / 'repo'; repo.mkdir(); marker = root / 'marker'
            (repo / 'demo.py').write_text('from pathlib import Path\nPath(' + repr(str(marker)) + ').touch()\ndef main(): pass\n')
            for index, (requirements, expected) in enumerate([
                ([], True), (['packaging==' + version('packaging')], True),
                (['packaging==999999999'], False), (['g2h-nonexistent-distribution-xyz>=1'], False),
            ]):
                marker.unlink(missing_ok=True)
                (repo / 'pyproject.toml').write_text('[project]\nname="demo"\nversion="1"\ndependencies=' + json.dumps(requirements) + '\n[project.scripts]\ndemo="demo:main"\n')
                output = Path(fixed_adapter.generate(repo, root/'out', 'demo'+str(index), {'type':'cli'})['output_path'])
                self.assertFalse(marker.exists())
                spec = importlib.util.spec_from_file_location('test_fixed_dep' + str(index), output/'adapter.py')
                module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
                result = json.loads(module.handle({}))
                self.assertEqual(result['success'], expected, result)
                self.assertEqual(marker.exists(), expected, result)
                self.assertEqual(json.loads((output/'plugin.json').read_text())['extensions']['com.nousresearch.hermes']['declaredRequirements'], requirements)
                for requirement in requirements: self.assertIn(requirement, (output/'README.md').read_text())

    def test_rejects_unsupported_requirements_before_output(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); repo = root/'repo'; repo.mkdir()
            (repo/'demo.py').write_text('def main(): pass\n')
            for i, requirement in enumerate(['pkg @ https://example.invalid/a', '../local', 'pkg[extra]', 'pkg; python_version>"3"', 'pkg>=1.*', 'pkg===1', 'pkg=>1', 'git+https://example.invalid/x', 'pkg>=1,\n<2', 'pkg>=1,\r<2']):
                (repo/'pyproject.toml').write_text('[project]\nname="demo"\nversion="1"\ndependencies=' + json.dumps([requirement]) + '\n[project.scripts]\ndemo="demo:main"\n')
                with self.assertRaises(ValueError): fixed_adapter.generate(repo, root/'out', 'bad'+str(i), {'type':'cli'})
                self.assertFalse((root/'out'/('bad'+str(i))).exists())
