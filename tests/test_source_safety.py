import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('g2h_detector_safety', ROOT / 'detector.py')
detector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(detector)


class SourceSafetyTests(unittest.TestCase):
    def test_untrusted_git_url_rejected_before_any_sink(self):
        for source in ('--upload-pack=evil', 'file:///outside', 'https://evil.example/a/b', 'https://github.com/a/b?token=secret', 'https://user:pass@github.com/a/b'):
            with self.subTest(source=source), patch.object(detector.tempfile, 'mkdtemp') as temp, patch.object(detector, '_run') as run:
                with self.assertRaises(ValueError):
                    detector.clone_repo(source)
                temp.assert_not_called()
                run.assert_not_called()

    def test_existing_output_is_rejected_before_generator_or_subprocess(self):
        spec = importlib.util.spec_from_file_location('g2h_tools_safety', ROOT / 'tools.py')
        tools = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tools)
        report = {'type': 'cli', 'repo_path': str(ROOT), 'suggested_name': 'existing'}
        with patch.object(tools.detector, 'detect_repo', return_value=report), \
             patch.object(tools.Path, 'exists', return_value=True), \
             patch.object(tools, '_load_module') as loader, \
             patch.object(tools.subprocess, 'run') as run:
            result = json.loads(tools.handle_convert_repo({'repo_url': 'https://github.com/a/b', 'name': 'existing', 'output_dir': str(ROOT)}))
        self.assertFalse(result['success'])
        loader.assert_not_called()
        run.assert_not_called()

    def test_explicit_destination_is_never_deleted(self):
        with patch.object(detector.os.path, 'exists', return_value=True), patch('shutil.rmtree') as delete, patch.object(detector.os, 'makedirs'), patch.object(detector, '_run') as run:
            with self.assertRaises(ValueError):
                detector.clone_repo('https://github.com/owner/repo', 'somewhere')
            delete.assert_not_called()
            run.assert_not_called()
