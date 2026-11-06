import importlib.util
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch

import detector
from generators import fixed_adapter


class RuntimeHardeningTests(unittest.TestCase):
    def test_local_git_clone_with_sanitized_environment_and_cleanup(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); src = root / 'repo'; src.mkdir()
            subprocess.run(['git', 'init', '-q', str(src)], check=True, capture_output=True)
            subprocess.run(['git', '-C', str(src), 'config', 'user.email', 'fixture@example.invalid'], check=True)
            subprocess.run(['git', '-C', str(src), 'config', 'user.name', 'Fixture'], check=True)
            (src / 'demo.py').write_text('def main(): return 0\n')
            subprocess.run(['git', '-C', str(src), 'add', 'demo.py'], check=True)
            subprocess.run(['git', '-C', str(src), 'commit', '-qm', 'fixture'], check=True)
            original = detector._run
            captured = []
            def local_clone(cmd, **kwargs):
                cmd = list(cmd); cmd[-2] = str(src); captured.append(cmd[-1])
                return original(cmd, **kwargs)
            with patch.object(detector, '_run', side_effect=local_clone), patch.dict(os.environ, {'GIT_CONFIG_COUNT':'1', 'GIT_CONFIG_KEY_0':'core.hooksPath', 'GIT_CONFIG_VALUE_0':'invalid'}):
                result = detector.detect_repo('https://github.com/test/fixture')
            self.assertEqual(result['type'], 'python_pkg')
            self.assertFalse(Path(captured[0]).exists())

    def test_detect_generate_register_and_invoke_real_manager(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); src = root / 'src'; src.mkdir()
            (src / 'pyproject.toml').write_text('[project]\nname="demo"\nversion="1"\n[project.scripts]\ndemo="demo:main"\n')
            (src / 'demo.py').write_text('def main():\n    print("real-manager-fixture")\n')
            report = detector.detect_type(str(src))
            self.assertEqual(report['type'], 'cli')
            paths = [fixed_adapter.generate(str(src), str(root / 'out'), name, report)['output_path'] for name in ('demo-a', 'demo.a')]
            code = '''import json,sys
from hermes_cli.agent_plugins import load_agent_plugin
from pathlib import Path
import importlib.util
answers=[]
for i,path in enumerate(sys.argv[1:]):
    package=load_agent_plugin(Path(path), Path(path)/'data')
    assert not package.diagnostics, package.diagnostics
    server=next(iter(package.mcp_servers.values()))
    assert server['trust']=='untrusted'
    spec=importlib.util.spec_from_file_location('fixture'+str(i), Path(path)/'adapter.py')
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    answers.append([module.TOOL, module.TOOLSET, json.loads(module.handle({}))])
print(json.dumps(answers))
'''
            env = dict(os.environ, HERMES_HOME=str(root / 'home'), PYTHONDONTWRITEBYTECODE='1')
            proc = subprocess.run([sys.executable, '-c', code, *paths], cwd=str(Path(sys.prefix).parent), env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            answers = json.loads(proc.stdout)
            self.assertNotEqual(answers[0][1], answers[1][1])
            for name, toolset, result in answers:
                self.assertTrue(result['success'], result)
                self.assertEqual(result['output'].strip(), 'real-manager-fixture')

    def test_clone_sanitizes_environment_and_cleans_failure(self):
        captured = {}
        def fail(cmd, **kw):
            captured.update(kw)
            captured['dest'] = cmd[-1]
            return 1, '', ''
        with patch.dict(os.environ, {'GIT_CONFIG_COUNT':'1', 'GIT_CONFIG_KEY_0':'url.file:///.insteadOf', 'GIT_CONFIG_VALUE_0':'https://github.com/'}, clear=False), patch.object(detector, '_run', side_effect=fail):
            with self.assertRaises(RuntimeError):
                detector.clone_repo('https://github.com/test/fixture')
        self.assertFalse(Path(captured['dest']).exists())
        self.assertNotIn('GIT_CONFIG_COUNT', captured['env'])
        self.assertEqual(captured['env']['GIT_TERMINAL_PROMPT'], '0')
        self.assertEqual(captured['env']['GIT_CONFIG_GLOBAL'], os.devnull)

    def test_output_limits_and_distinct_names(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); src = root / 'src'; src.mkdir()
            (src / 'pyproject.toml').write_text('[project]\nname="demo"\nversion="1"\n[project.scripts]\ndemo="demo:main"\n')
            (src / 'demo.py').write_text('import sys\ndef main():\n    sys.stdout.write("a" * 1200000)\n    sys.stderr.write("b" * 10000)\n')
            modules = []
            for name in ['demo-a', 'demo_a']:
                result = fixed_adapter.generate(str(src), str(root / 'out'), name, {'type':'cli'})
                spec = importlib.util.spec_from_file_location('generated_' + str(len(modules)), Path(result['output_path']) / 'adapter.py')
                module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); modules.append(module)
            self.assertNotEqual(modules[0].TOOLSET, modules[1].TOOLSET)
            self.assertLessEqual(len(modules[0].TOOL), 64)
            answer = json.loads(modules[0].handle({}))
            self.assertFalse(answer['success'])
            self.assertEqual(answer['error'], 'output limit exceeded')

    def test_timeout_kills_direct_child(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); src = root / 'src'; src.mkdir()
            (src / 'pyproject.toml').write_text('[project]\nname="demo"\nversion="1"\n[project.scripts]\ndemo="demo:main"\n')
            (src / 'demo.py').write_text('import time\ndef main(): time.sleep(10)\n')
            result = fixed_adapter.generate(str(src), str(root / 'out'), 'timeout', {'type':'cli'})
            spec = importlib.util.spec_from_file_location('generated_timeout', Path(result['output_path']) / 'adapter.py')
            module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
            with patch.object(module.time, 'monotonic', side_effect=[0, 31, 31]):
                answer = json.loads(module.handle({}))
            self.assertEqual(answer['error'], 'timeout')
