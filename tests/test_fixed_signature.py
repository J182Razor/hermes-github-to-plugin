import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import tools


class FixedSignatureTests(unittest.TestCase):
    def test_required_arguments_rejected_before_output_for_both_types(self):
        for kind in ('cli', 'python_pkg'):
            for signature in ('value', 'value, /', '*, value'):
                with self.subTest(kind=kind, signature=signature), tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
                    root = Path(tmp); src = root / 'src'; src.mkdir()
                    metadata = '[project]\nname="demo"\nversion="1"\n'
                    metadata += '[project.scripts]\ndemo="demo:main"\n' if kind == 'cli' else '[tool.github-to-hermes]\npython-entrypoint="demo:main"\n'
                    (src / 'pyproject.toml').write_text(metadata)
                    (src / 'demo.py').write_text(f'def main({signature}):\n    return 42\n')
                    report = {'type': kind, 'repo_path': str(src), 'suggested_name': 'demo'}
                    with patch.object(tools.detector, 'detect_repo', return_value=report):
                        result = json.loads(tools.handle_convert_repo({'repo_url': 'https://github.com/a/b', 'output_dir': str(root / 'out')}))
                    self.assertFalse(result['success'], result)
                    self.assertFalse((root / 'out').exists())

    def test_unsafe_or_ambiguous_signatures_are_rejected(self):
        sources = (
            'def main(value=print("side effect")): pass\n',
            'def main(*, value=print("side effect")): pass\n',
            '@staticmethod\ndef main(): pass\n',
            'async def main(): pass\n',
            'def main(): pass\nmain = 3\n',
        )
        for kind in ('cli', 'python_pkg'):
            for source in sources:
                with self.subTest(kind=kind, source=source), tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
                    root = Path(tmp); src = root / 'src'; src.mkdir()
                    metadata = '[project]\nname="demo"\nversion="1"\n'
                    metadata += '[project.scripts]\ndemo="demo:main"\n' if kind == 'cli' else '[tool.github-to-hermes]\npython-entrypoint="demo:main"\n'
                    (src / 'pyproject.toml').write_text(metadata)
                    (src / 'demo.py').write_text(source)
                    report = {'type': kind, 'repo_path': str(src), 'suggested_name': 'demo'}
                    with patch.object(tools.detector, 'detect_repo', return_value=report):
                        result = json.loads(tools.handle_convert_repo({'repo_url': 'https://github.com/a/b', 'output_dir': str(root / 'out')}))
                    self.assertFalse(result['success'], result)
                    self.assertFalse((root / 'out').exists())

    def test_defaulted_arguments_execute_through_generated_handler(self):
        for kind in ('cli', 'python_pkg'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
                root = Path(tmp); src = root / 'src'; src.mkdir()
                metadata = '[project]\nname="demo"\nversion="1"\n'
                metadata += '[project.scripts]\ndemo="demo:main"\n' if kind == 'cli' else '[tool.github-to-hermes]\npython-entrypoint="demo:main"\n'
                (src / 'pyproject.toml').write_text(metadata)
                (src / 'demo.py').write_text('def main(value=42, /, *, suffix="ok"):\n    print(value, suffix)\n')
                report = {'type': kind, 'repo_path': str(src), 'suggested_name': 'demo'}
                with patch.object(tools.detector, 'detect_repo', return_value=report):
                    result = json.loads(tools.handle_convert_repo({'repo_url': 'https://github.com/a/b', 'output_dir': str(root / 'out')}))
                self.assertTrue(result['success'], result)
                spec = importlib.util.spec_from_file_location('signature_adapter', Path(result['output_path']) / 'adapter.py')
                module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
                answer = json.loads(module.handle({}))
                self.assertTrue(answer['success'], answer)
                self.assertIn('42 ok', answer['output'])
