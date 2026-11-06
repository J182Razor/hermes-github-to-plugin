import json
import os
from pathlib import Path
import tempfile
import unittest

import detector


class DetectionPrivacyTests(unittest.TestCase):
    def test_script_values_and_readme_query_are_not_exposed(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp)
            secret = 'fixture-sensitive-value-abcdef'
            (root / 'package.json').write_text(json.dumps({'scripts': {'mcp': 'node server.js --token=' + secret}}))
            (root / 'README.md').write_text('MCP server\nGET /items?token=' + secret)
            report = detector.detect_type(str(root))
            self.assertNotIn(secret, json.dumps(report))

    def test_readme_secret_like_values_are_not_reported_as_environment_names(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp)
            secret = 'TOKEN_SECRET_FIXTURE_VALUE'
            (root / 'README.md').write_text('Set token to ' + secret)
            self.assertNotIn(secret, json.dumps(detector.detect_type(str(root))))
