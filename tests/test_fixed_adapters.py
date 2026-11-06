import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import tools

class FixedAdapterTests(unittest.TestCase):
    def test_declared_dependencies_are_reported_without_executing_source(self):
        import detector
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); marker = root / 'executed'
            (root / 'pyproject.toml').write_text('[project]\nname="demo"\nversion="1"\ndependencies=["click>=8.1"]\n[project.scripts]\ndemo="demo:main"\n')
            (root / 'demo.py').write_text('from pathlib import Path\nPath(' + repr(str(marker)) + ').touch()\ndef main(): pass\n')
            report = detector.detect_type(str(root))
            evidence = str(report)
            self.assertNotIn('supported fixed Python CLI entrypoint', evidence)
            self.assertIn('manual provisioning', evidence)
            report['repo_path'] = str(root)
            result = json.loads(tools._convert_detected(report, '', 'demo', str(root / 'out')))
            self.assertTrue(result['success'], result)
            manifest = json.loads((Path(result['output_path']) / 'plugin.json').read_text())
            self.assertEqual(manifest['extensions']['com.nousresearch.hermes']['declaredRequirements'], ['click>=8.1'])
            self.assertFalse(marker.exists())

    def test_python_generated_handler_executes_declared_callable_only_on_invocation(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); src = root / 'src'; src.mkdir()
            marker = root / 'ran'
            (src / 'pyproject.toml').write_text('[project]\nname="demo"\nversion="1.0"\n[tool.github-to-hermes]\npython-entrypoint="demo:main"\n')
            (src / 'demo.py').write_text('from pathlib import Path\ndef main():\n    Path(' + repr(str(marker)) + ').write_text("ran")\n    return 42\n')
            report = {'type':'python_pkg', 'repo_path':str(src), 'suggested_name':'demo'}
            with patch.object(tools.detector, 'detect_repo', return_value=report):
                result = json.loads(tools.handle_convert_repo({'repo_url':'https://github.com/a/b', 'output_dir':str(root / 'out')}))
            self.assertTrue(result['success'], result)
            self.assertFalse(marker.exists())
            spec = importlib.util.spec_from_file_location('fixture_python_adapter', Path(result['output_path']) / 'adapter.py')
            module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
            self.assertFalse(marker.exists())
            answer = json.loads(module.handle({}))
            self.assertTrue(answer['success'], answer)
            self.assertEqual(answer['output'].strip(), '42')
            self.assertTrue(marker.is_file())

    def test_cli_generated_handler_executes_fixed_local_fixture(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); src = root / 'src'; src.mkdir()
            (src / 'pyproject.toml').write_text('[project]\nname="demo"\nversion="1.0"\n[project.scripts]\ndemo="demo:main"\n')
            (src / 'demo.py').write_text('def main():\n    print("fixture-result")\n')
            report = {'type':'cli', 'repo_path':str(src), 'suggested_name':'demo'}
            with patch.object(tools.detector, 'detect_repo', return_value=report):
                result = json.loads(tools.handle_convert_repo({'repo_url':'https://github.com/a/b', 'output_dir':str(root / 'out')}))
            self.assertTrue(result['success'], result)
            spec = importlib.util.spec_from_file_location('fixture_adapter', Path(result['output_path']) / 'adapter.py')
            module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
            answer = json.loads(module.handle({}))
            self.assertTrue(answer['success'], answer)
            self.assertIn('fixture-result', answer['output'])
