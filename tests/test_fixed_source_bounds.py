import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from generators import fixed_adapter


class FixedSourceBounds(unittest.TestCase):
    def test_oversized_source_read_is_bounded_before_worker(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); src = root / 'src'; src.mkdir()
            (src / 'pyproject.toml').write_text('[project]\nname="demo"\nversion="1"\n[project.scripts]\ndemo="demo:main"\n')
            (src / 'demo.py').write_text('def main(): pass\n')
            out = Path(fixed_adapter.generate(src, root / 'out', 'demo', {'type':'cli'})['output_path'])
            spec = importlib.util.spec_from_file_location('bounded_fixture', out / 'adapter.py')
            adapter = importlib.util.module_from_spec(spec); spec.loader.exec_module(adapter)
            with (out / 'source.py').open('wb') as stream:
                stream.truncate(8 * 1024 * 1024)
            original_fdopen = os.fdopen
            reads = []
            class Reader:
                def __init__(self, stream): self.stream = stream
                def __enter__(self): return self
                def __exit__(self, *args): return self.stream.__exit__(*args)
                def fileno(self): return self.stream.fileno()
                def read(self, size=-1):
                    reads.append(size)
                    return self.stream.read(size)
            with patch.object(adapter.os, 'fdopen', side_effect=lambda *a, **k: Reader(original_fdopen(*a, **k))), patch.object(adapter, '_run_contained') as worker:
                result = json.loads(adapter.handle({}))
            self.assertFalse(result['success'])
            self.assertEqual(reads, [1024 * 1024 + 1])
            worker.assert_not_called()
