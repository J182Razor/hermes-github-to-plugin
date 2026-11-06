import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import subprocess

from generators import mcp_safe, skill_bundle


class PortableLoaderTests(unittest.TestCase):
    def test_rest_rejects_nested_references_before_writes(self):
        from generators import rest_safe
        from unittest.mock import patch
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp)
            (root / 'openapi.json').write_text(json.dumps({'openapi':'3.0.0', 'paths':{'/x':{'get':{'operationId':'x', 'responses':{'200':{'$ref':'#/components/responses/X'}}}}}}))
            with patch.object(Path, 'mkdir', side_effect=AssertionError('write sink reached')):
                with self.assertRaises(ValueError):
                    rest_safe.generate(str(root), str(root / 'out'), 'demo', {})

    def test_pyproject_cli_detects_as_cli(self):
        import detector
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp)
            (root / 'pyproject.toml').write_text('[project]\nname="demo"\nversion="1"\n[project.scripts]\ndemo="demo:main"\n')
            (root / 'demo.py').write_text('def main(): return 0\n')
            self.assertEqual(detector.detect_type(str(root))['type'], 'cli')

    def test_real_loader_discovers_generated_packages(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp)
            src = root / 'src'
            src.mkdir()
            skill = src / 'skills' / 'demo'
            skill.mkdir(parents=True)
            (skill / 'SKILL.md').write_text('---\nname: demo\ndescription: demo\n---\nHello\n')
            (src / 'server.py').write_text('print("benign")')
            (src / 'mcp.json').write_text(json.dumps({'mcpServers': {'demo': {'command': 'python', 'args': ['server.py']}}}))
            for generator, name in ((skill_bundle, 'skills-demo'),):
                result = generator.generate(str(src), str(root / 'out'), name, {})
                code = ('import json,sys; from pathlib import Path; from hermes_cli.agent_plugins import load_agent_plugin; '
                        'p=load_agent_plugin(Path(sys.argv[1]),Path(sys.argv[2])); '
                        'print(json.dumps({"diagnostics":[str(d) for d in p.diagnostics],"skills":len(p.skills),"servers":list(p.mcp_servers)}))')
                env = dict(os.environ, HERMES_HOME=str(root / 'home'), PYTHONDONTWRITEBYTECODE='1')
                proc = subprocess.run([sys.executable, '-c', code, result['output_path'], str(root / 'data')], cwd=str(Path(sys.prefix).parent), env=env, capture_output=True, text=True, timeout=30)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                package = json.loads(proc.stdout)
                self.assertFalse(package['diagnostics'], package['diagnostics'])
                if generator is skill_bundle:
                    self.assertEqual(package['skills'], 1)
                else:
                    self.assertIn('demo', package['servers'])
