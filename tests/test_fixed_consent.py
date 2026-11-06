import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from generators import fixed_adapter


class FixedConsentTests(unittest.TestCase):
    def test_tampered_source_never_reaches_execution_sink(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp)
            (root / 'pyproject.toml').write_text('[project]\nname="demo"\nversion="1"\n[project.scripts]\ndemo="demo:main"\n')
            (root / 'demo.py').write_text('def main(): return 0\n')
            result = fixed_adapter.generate(str(root), str(root / 'out'), 'demo', {'type':'cli'})
            spec = importlib.util.spec_from_file_location('consent_fixture', Path(result['output_path']) / 'adapter.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            (Path(result['output_path']) / 'source.py').write_text('tampered')
            with patch.object(module.subprocess, 'Popen', side_effect=AssertionError('execution sink reached')) as sink:
                answer = json.loads(module.handle({}))
            self.assertFalse(answer['success'])
            self.assertIn('digest mismatch', answer['error'])
            sink.assert_not_called()
