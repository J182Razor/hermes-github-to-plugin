import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import detector
from generators import fixed_adapter, mcp_safe, rest_safe

class AuditFollowup(unittest.TestCase):
    def test_windows_clone_fails_before_subprocess_when_acl_unavailable(self):
        with patch.object(detector.os, 'name', 'nt'), patch.object(detector, '_windows_private_clone_dir', side_effect=RuntimeError('unavailable')), patch.object(detector, '_run') as run:
            with self.assertRaises(RuntimeError):
                detector.clone_repo('https://github.com/example/example')
            run.assert_not_called()

    def test_get_requires_operator_approved_operation(self):
        import runpy
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); src = root / 'src'; src.mkdir(); data = root / 'data'; data.mkdir()
            (src / 'openapi.json').write_text(json.dumps({'openapi':'3.0.0', 'paths':{'/delete-all':{'get':{'operationId':'delete_all'}}}}))
            out = Path(rest_safe.generate(src, root / 'out', 'demo', {})['output_path'])
            (data / 'openapi-authorization.json').write_text(json.dumps({'base_url':'http://127.0.0.1', 'operations':[]}))
            with patch.dict(os.environ, {'PLUGIN_DATA':str(data)}): server = runpy.run_path(str(out / 'server.py'))
            with patch.dict(server['invoke'].__globals__, {'build_opener': unittest.mock.Mock(side_effect=AssertionError('network reached'))}):
                result = server['invoke'](server['OPERATIONS'][0], {})
                self.assertFalse(result['success']); server['invoke'].__globals__['build_opener'].assert_not_called()

    def test_detection_does_not_disclose_untrusted_names(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp) / 'secret-root-fixture'; root.mkdir()
            (root / 'secret-module-fixture.py').write_text('')
            pkg = root / 'secret-package-fixture'; pkg.mkdir(); (pkg / '__init__.py').write_text('')
            skill = root / 'skills' / 'secret-skill-fixture'; skill.mkdir(parents=True); (skill / 'SKILL.md').write_text('')
            report = json.dumps(detector.detect_type(str(root)))
            self.assertNotIn('secret-', report)

    def test_missing_function_rejected_before_output(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); src = root / 'src'; src.mkdir()
            (src / 'pyproject.toml').write_text('[project]\nname="demo"\nversion="1"\n[project.scripts]\ndemo="demo:main"\n')
            (src / 'demo.py').write_text('def other(): pass\n')
            with self.assertRaises(ValueError):
                fixed_adapter.generate(src, root / 'out', 'demo', {'type':'cli'})
            self.assertFalse((root / 'out').exists())

    def test_dynamic_rebinding_rejected(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); src = root / 'src'; src.mkdir()
            (src / 'pyproject.toml').write_text('[project]\nname="demo"\nversion="1"\n[project.scripts]\ndemo="demo:main"\n')
            (src / 'demo.py').write_text('def main(): pass\nglobals()["main"] = 42\n')
            with self.assertRaises(ValueError):
                fixed_adapter.generate(src, root / 'out', 'demo', {'type':'cli'})

    def test_mcp_environment_override_refused_before_output(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); src = root / 'src'; src.mkdir()
            (src / 'mcp.json').write_text(json.dumps({'mcpServers': {'demo': {'command':'python', 'args':['server.py'], 'env':{'DANGEROUS':'literal'}}}}))
            (src / 'server.py').write_text('raise RuntimeError("must not run")')
            with self.assertRaises(ValueError):
                mcp_safe.generate(src, root / 'out', 'demo', {'type':'mcp'})
            self.assertFalse((root / 'out').exists())
