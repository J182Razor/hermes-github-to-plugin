import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import detector
from generators import rest_safe

class SecurityGaps(unittest.TestCase):
    def test_tied_detection_fails_closed(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as t:
            root=Path(t)
            (root/'mcp.json').write_text('{}')
            (root/'openapi.json').write_text('{}')
            result=detector.detect_type(t)
            self.assertEqual(result['type'], 'unknown')
            self.assertIn('ambiguous', ' '.join(result['evidence']).lower())

    def test_rest_rejects_unapproved_destination_before_network(self):
        import runpy
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as t:
            root=Path(t); (root/'openapi.json').write_text(json.dumps({'openapi':'3.0.0','paths':{'/x':{'get':{'operationId':'x'}}}}))
            out=Path(rest_safe.generate(t,str(root/'out'),'demo',{})['output_path']); data=root/'data'; data.mkdir()
            (data/'openapi-authorization.json').write_text(json.dumps({'base_url':'https://api.example.com', 'operations':['GET /x']}))
            with patch.dict(os.environ, {'PLUGIN_DATA':str(data)}): server=runpy.run_path(str(out/'server.py'))
            with patch.dict(server['invoke'].__globals__, {'build_opener': unittest.mock.Mock(side_effect=AssertionError('network reached'))}):
                response=server['invoke'](server['OPERATIONS'][0], {'base_url':'http://127.0.0.1:80'})
                self.assertFalse(response['success']); server['invoke'].__globals__['build_opener'].assert_not_called()
