import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from generators import rest_safe


class RequestBodyReferences(unittest.TestCase):
    def test_reachable_rejections_and_unused_components(self):
        body = {'required': True, 'content': {'application/json': {'schema': {
            'type': 'object', 'properties': {'message': {'type': 'string'}}}}}}
        base = {'openapi': '3.0.0', 'components': {'requestBodies': {'Body': body}},
                'paths': {'/x': {'post': {'operationId': 'x', 'requestBody': {'$ref': '#/components/requestBodies/Body'}}}}}
        cases = []
        for ref in ('https://example.invalid/body', '#/components/requestBodies/Missing',
                    '#/components/requestBodies/Body~2', '#/components/requestBodies/Body%2F',
                    '#/components/requestBodies/Body/content', '#/components/requestBodies/'):
            spec = copy.deepcopy(base); spec['paths']['/x']['post']['requestBody'] = {'$ref': ref}; cases.append(spec)
        spec = copy.deepcopy(base); spec['paths']['/x']['post']['requestBody']['description'] = 'sibling'; cases.append(spec)
        spec = copy.deepcopy(base); spec['components']['requestBodies']['Body'] = {'$ref': '#/components/requestBodies/Body'}; cases.append(spec)
        spec = copy.deepcopy(base); spec['components']['requestBodies'].update({'Body': {'$ref': '#/components/requestBodies/Other'}, 'Other': {'$ref': '#/components/requestBodies/Body'}}); cases.append(spec)
        spec = copy.deepcopy(base); spec['components']['requestBodies']['Body']['content'] = {'multipart/form-data': {'schema': {'type': 'object'}}}; cases.append(spec)
        spec = copy.deepcopy(base); bodies = spec['components']['requestBodies']; bodies['Body'] = {'blob': 'x' * 50000}
        for i in range(8):
            ref = '#/components/requestBodies/' + ('Body' if i == 0 else 'R' + str(i - 1))
            bodies['R' + str(i)] = {'a': {'$ref': ref}, 'b': {'$ref': ref}}
        spec['paths']['/x']['post']['requestBody'] = {'$ref': '#/components/requestBodies/R7'}; cases.append(spec)
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); source = root / 'openapi.json'
            for i, spec in enumerate(cases):
                with self.subTest(case=i):
                    source.write_text(json.dumps(spec))
                    with patch.object(Path, 'mkdir', side_effect=AssertionError('output reached')) as sink, patch('urllib.request.urlopen', side_effect=AssertionError('network reached')) as network:
                        with self.assertRaisesRegex(ValueError, 'expanded size exceeded' if i == len(cases) - 1 else '.+'): rest_safe.generate(root, root / 'out', 'demo', {})
                        sink.assert_not_called(); network.assert_not_called()
            spec = copy.deepcopy(base)
            spec['components']['requestBodies']['Unused'] = {'$ref': '#/components/requestBodies/Unused'}
            spec['components']['requestBodies']['UnusedMedia'] = {'content': {'multipart/form-data': {}}}
            source.write_text(json.dumps(spec))
            with patch.object(Path, 'mkdir', side_effect=RuntimeError('output reached')):
                with self.assertRaisesRegex(RuntimeError, 'output reached'): rest_safe.generate(root, root / 'out', 'demo', {})

    def test_duplicate_request_body_key_rejected(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp)
            (root / 'openapi.json').write_text('{"openapi":"3.0.0","components":{"requestBodies":{"B":{},"B":{}}},"paths":{"/x":{"get":{"operationId":"x"}}}}')
            with patch.object(Path, 'mkdir', side_effect=AssertionError('output reached')):
                with self.assertRaisesRegex(ValueError, 'duplicate'): rest_safe.generate(root, root / 'out', 'demo', {})
