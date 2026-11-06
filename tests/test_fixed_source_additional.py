import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from generators import fixed_adapter


class FixedSourceAdditional(unittest.TestCase):
    def test_source_states_and_output(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); src = root / 'src'; src.mkdir()
            (src / 'pyproject.toml').write_text('[project]\nname="demo"\nversion="1"\n[project.scripts]\ndemo="demo:main"\n')
            (src / 'demo.py').write_text('import sys\ndef main():\n print("stdout-fixture")\n print("stderr-fixture", file=sys.stderr)\n')
            out = Path(fixed_adapter.generate(src, root / 'out', 'demo', {'type':'cli'})['output_path'])
            spec = importlib.util.spec_from_file_location('source_fixture', out / 'adapter.py')
            adapter = importlib.util.module_from_spec(spec); spec.loader.exec_module(adapter)
            result = json.loads(adapter.handle({}))
            self.assertTrue(result['success'], result)
            self.assertIn('stdout-fixture', result['output'])
            self.assertIn('stderr-fixture', result['stderr'])
            target = out / 'source.py'
            target.write_text('def main(): pass\n')
            with patch.object(adapter, '_run_contained') as worker:
                self.assertIn('digest mismatch', json.loads(adapter.handle({}))['error'])
                target.unlink()
                self.assertFalse(json.loads(adapter.handle({}))['success'])
                try:
                    target.symlink_to(src / 'demo.py')
                except OSError:
                    pass
                else:
                    self.assertIn('non-symlink', json.loads(adapter.handle({}))['error'])
                worker.assert_not_called()

    def test_reparse_attributes_rejected(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); src = root / 'src'; src.mkdir()
            (src / 'pyproject.toml').write_text('[project]\nname="demo"\nversion="1"\n[project.scripts]\ndemo="demo:main"\n')
            (src / 'demo.py').write_text('def main(): pass\n')
            out = Path(fixed_adapter.generate(src, root / 'out', 'demo', {'type':'cli'})['output_path'])
            spec = importlib.util.spec_from_file_location('reparse_fixture', out / 'adapter.py')
            adapter = importlib.util.module_from_spec(spec); spec.loader.exec_module(adapter)
            from types import SimpleNamespace
            with patch.object(Path, 'lstat', return_value=SimpleNamespace(st_mode=0o100644, st_file_attributes=0x400)), patch.object(adapter, '_run_contained') as worker:
                self.assertIn('non-symlink', json.loads(adapter.handle({}))['error'])
                worker.assert_not_called()
