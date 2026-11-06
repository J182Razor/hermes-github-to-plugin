import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import tools
import detector
from generators import rest_safe


class FilesystemRegressions(unittest.TestCase):
    def test_swagger_yml_is_detected_but_not_converted(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            (Path(tmp) / 'swagger.yml').write_text('swagger: "2.0"')
            report = detector.detect_type(tmp)
            self.assertEqual(report['type'], 'rest')
            with patch.object(Path, 'mkdir') as mkdir, patch.object(Path, 'write_text') as write, patch('shutil.rmtree') as delete:
                with self.assertRaises(ValueError):
                    rest_safe.generate(tmp, str(Path(tmp) / 'out'), 'demo', report)
                mkdir.assert_not_called(); write.assert_not_called(); delete.assert_not_called()

    def test_output_reparse_component_rejected_before_generation(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp)
            component = root / 'junction'
            component.mkdir()
            original = Path.lstat
            def marked(path):
                st = original(path)
                if path == component:
                    class Reparse:
                        st_mode = st.st_mode
                        st_file_attributes = 0x400
                    return Reparse()
                return st
            report = {'repo_path': tmp, 'type': 'rest'}
            with patch.object(Path, 'lstat', marked), patch.object(tools, '_load_module') as sink:
                sink.return_value.generate.return_value = {'output_path': 'mock'}
                result = json.loads(tools._convert_detected(report, '', 'demo', str(component)))
            self.assertFalse(result['success'])
            sink.assert_not_called()

    def test_path_parameters_and_refs_rejected_before_output(self):
        cases = [
            {'/items/{id}': {'get': {'operationId': 'getItem'}}},
            {'/items': {'get': {'operationId': 'getItem', 'responses': {'200': {'$ref': 'https://example.invalid/spec'}}}}},
            {'/items': {'get': {'operationId': 'getItem', 'servers': [{'url': 'https://example.invalid'}]}}},
        ]
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp)
            for paths in cases:
                (root / 'openapi.json').write_text(json.dumps({'openapi': '3.0.0', 'paths': paths}))
                with self.subTest(paths=paths), patch.object(Path, 'mkdir') as mkdir, patch.object(Path, 'write_text') as write, patch('shutil.rmtree') as delete:
                    with self.assertRaises(ValueError):
                        rest_safe.generate(tmp, str(root / 'out'), 'demo', {})
                    mkdir.assert_not_called()
                    write.assert_not_called()
                    delete.assert_not_called()
