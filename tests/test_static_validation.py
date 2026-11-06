import builtins
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import static_validation as sv


class StaticValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ['TMPDIR'])
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write(self, name, content):
        (self.root / name).write_text(content, encoding='utf-8')

    def portable(self):
        self.write('plugin.json', json.dumps({'$schema': sv.PLUGIN_SCHEMA, 'name': 'demo', 'extensions': {'com.nousresearch.hermes': {'servers': {'demo': {'trust': 'untrusted'}}}}}))
        self.write('mcp.json', json.dumps({'$schema': sv.MCP_SCHEMA, 'mcpServers': {'demo': {'type': 'stdio', 'command': 'python', 'args': ['${PLUGIN_ROOT}/server.py']}}}))
        self.write('server.py', 'raise Exception("must not run")')

    def test_native_no_execution(self):
        self.write('plugin.yaml', 'name: demo\nprovides_tools: [one]\n')
        marker = self.root / 'executed'
        self.write('__init__.py', f'open({str(marker)!r}, "w").write("bad")')
        with patch.object(subprocess, 'Popen', side_effect=AssertionError('execution')):
            result = sv.validate_static(self.root)
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['static_only'])
        self.assertEqual(result['runtime_validation'], 'not run')
        self.assertFalse(marker.exists())

    def test_portable_and_traversal(self):
        self.portable()
        self.assertTrue(sv.validate_static(self.root)['ok'])
        data = json.loads((self.root / 'mcp.json').read_text())
        data['mcpServers']['demo']['args'] = ['${PLUGIN_ROOT}/../outside']
        self.write('mcp.json', json.dumps(data))
        self.assertFalse(sv.validate_static(self.root)['ok'])

    def test_duplicate_and_malformed_json_and_yaml(self):
        for name, content in [('plugin.json', '{"name":"secret-canary","name":"x"}'), ('plugin.json', '{"bad":'), ('plugin.yaml', 'name: a\nname: secret-canary\n'), ('plugin.yaml', 'name: [')]:
            with self.subTest(name=name, content=content):
                for f in ('plugin.json', 'plugin.yaml'):
                    (self.root / f).unlink(missing_ok=True)
                self.write(name, content)
                result = sv.validate_static(self.root)
                self.assertFalse(result['ok'])
                self.assertNotIn('secret-canary', str(result))

    def test_nested_duplicate_keys_and_yaml_alias(self):
        # This source is deliberately executable only as a side-effect canary.
        marker = self.root / 'source-executed'
        self.write('__init__.py', f'open({str(marker)!r}, "w").write("executed")')
        cases = (
            ('plugin.yaml', 'name: demo\nconfig_schema:\n  option:\n    key: one\n    key: two\n'),
            ('plugin.yaml', 'name: demo\nconfig_schema:\n  option:\n    first: &value one\n    second: *value\n'),
            ('plugin.json', '{"$schema": ' + json.dumps(sv.PLUGIN_SCHEMA) + ', "name":"demo", "author":{"name":"one","name":"two"}}'),
        )
        for name, content in cases:
            with self.subTest(manifest=name, content=content):
                for manifest in ('plugin.json', 'plugin.yaml'):
                    (self.root / manifest).unlink(missing_ok=True)
                self.write(name, content)
                with patch.object(subprocess, 'Popen', side_effect=AssertionError('execution')):
                    result = sv.validate_static(self.root)
                self.assertFalse(result['ok'], result)
                self.assertEqual(result['errors'], ['invalid, unreadable, oversized, or unsupported plugin metadata'])
                self.assertFalse(marker.exists())

    def test_excessively_nested_yaml_and_json_are_bounded(self):
        # Run pathological parser inputs in a time-bounded child, not plugin code.
        marker = self.root / 'source-executed'
        self.write('__init__.py', f'open({str(marker)!r}, "w").write("executed")')
        depth = 1500
        cases = (
            ('plugin.yaml', 'name: demo\nnested: ' + '[' * depth + '0' + ']' * depth + '\n'),
            ('plugin.json', '{"$schema":' + json.dumps(sv.PLUGIN_SCHEMA) + ',"name":"demo","nested":' + '[' * depth + '0' + ']' * depth + '}'),
        )
        env = dict(os.environ, PYTHONPATH=str(Path(sv.__file__).parent), PYTHONDONTWRITEBYTECODE='1')
        code = 'import json, static_validation, sys; print(json.dumps(static_validation.validate_static(sys.argv[1])))'
        for name, content in cases:
            with self.subTest(manifest=name):
                for manifest in ('plugin.json', 'plugin.yaml'):
                    (self.root / manifest).unlink(missing_ok=True)
                self.write(name, content)
                child = subprocess.run([sys.executable, '-c', code, str(self.root)],
                                       env=env, capture_output=True, text=True, timeout=20)
                self.assertEqual(child.returncode, 0, child.stderr)
                result = json.loads(child.stdout)
                self.assertFalse(result['ok'], result)
                self.assertEqual(result['errors'], ['invalid, unreadable, oversized, or unsupported plugin metadata'])
                self.assertFalse(marker.exists())

    def test_oversized(self):
        self.write('plugin.json', 'x' * (sv.MAX_FILE + 1))
        self.assertFalse(sv.validate_static(self.root)['ok'])

    def test_link_and_reparse(self):
        self.portable()
        original = Path.lstat
        target = self.root / 'mcp.json'
        def fake(path, *args, **kwargs):
            info = original(path, *args, **kwargs)
            if path == target:
                class Reparse:
                    st_mode = info.st_mode
                    st_file_attributes = 0x400
                return Reparse()
            return info
        with patch.object(Path, 'lstat', fake):
            self.assertFalse(sv.validate_static(self.root)['ok'])
        target.unlink()
        try:
            target.symlink_to(self.root / 'plugin.json')
        except OSError:
            return
        self.assertFalse(sv.validate_static(self.root)['ok'])

    def test_missing_yaml(self):
        self.write('plugin.yaml', 'name: demo\n')
        real = builtins.__import__
        def guarded(name, *args, **kwargs):
            if name == 'yaml':
                raise ImportError('missing')
            return real(name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=guarded):
            result = sv.validate_static(self.root)
        self.assertFalse(result['ok'])
        self.assertIn('PyYAML', result['errors'][0])

    def test_native_loader_from_unrelated_cwd(self):
        repo = Path(sv.__file__).parent
        self.write('metadata_io.py', 'raise RuntimeError("hijacked")')
        env = dict(os.environ, PYTHONPATH='', PYTHONDONTWRITEBYTECODE='1')
        code = '''import importlib.util, sys
spec = importlib.util.spec_from_file_location('_g2h_tools', sys.argv[1])
tools = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tools)
before = sys.path[:]
validator = tools._load_module('static_validation', 'static_validation.py')
assert validator.read_bounded.__module__ == '_g2h_metadata_io'
assert sys.path == before
assert validator.validate_static(sys.argv[2])['ok']
'''
        self.write('plugin.json', json.dumps({'$schema': sv.PLUGIN_SCHEMA, 'name': 'fixture'}))
        result = subprocess.run([sys.executable, '-c', code, str(repo / 'tools.py'), str(self.root)],
                                cwd=self.root, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_trust_and_unsupported_policy(self):
        self.portable()
        path = self.root / 'plugin.json'
        data = json.loads(path.read_text())
        decl = data['extensions']['com.nousresearch.hermes']['servers']['demo']
        for value in ('full', 'untrusted', 'bad', None, True, [], {}):
            with self.subTest(trust=value):
                decl['trust'] = value
                path.write_text(json.dumps(data))
                result = sv.validate_static(self.root)
                self.assertEqual(result['ok'], value in ('full', 'untrusted'), result)
        for field in ('timeout', 'costs', 'spend_limit', 'cost_unit', 'app', 'requires', 'liveness', 'unknown'):
            with self.subTest(field=field):
                decl.clear()
                decl[field] = 1
                path.write_text(json.dumps(data))
                self.assertIn('unsupported server declaration policy', sv.validate_static(self.root)['errors'])

    def test_plugin_data_cwd_is_explicitly_unsupported(self):
        self.portable()
        path = self.root / 'mcp.json'
        data = json.loads(path.read_text())
        for cwd in ('${PLUGIN_DATA}', '${PLUGIN_DATA}/work', '${PLUGIN_DATA}/../outside'):
            with self.subTest(cwd=cwd):
                data['mcpServers']['demo']['cwd'] = cwd
                path.write_text(json.dumps(data))
                result = sv.validate_static(self.root)
                self.assertFalse(result['ok'])
                self.assertIn('PLUGIN_DATA cwd is unsupported without an approved data root', result['errors'])
                self.assertFalse(result['checks'])

    def test_generated_artifacts_without_runtime_execution(self):
        from generators import cli, skill_bundle, rest_safe, mcp_safe
        src = self.root / 'src'
        src.mkdir()
        marker = self.root / 'executed'
        (src / 'server.py').write_text(f'open({str(marker)!r}, "w").write("bad")')
        (src / 'mcp.json').write_text(json.dumps({'mcpServers': {'demo': {'command': 'python', 'args': ['server.py']}}}))
        (src / 'openapi.json').write_text(json.dumps({'openapi': '3.0.0', 'paths': {'/items': {'get': {'operationId': 'items'}}}}))
        skill = src / 'skills' / 'demo'
        skill.mkdir(parents=True)
        (skill / 'SKILL.md').write_text('---\nname: demo\ndescription: demo\n---\n')
        out = self.root / 'out'
        for generator, name in ((cli, 'native'), (skill_bundle, 'skill'), (rest_safe, 'api'), (mcp_safe, 'mcp')):
            with self.subTest(generator=name):
                artifact = generator.generate(src, out, name, {})
                result = sv.validate_static(artifact['output_path'])
                self.assertTrue(result['ok'], result)
                self.assertTrue(result['static_only'])
                self.assertEqual(result['runtime_validation'], 'not run')
                self.assertTrue(result['warnings'])
        self.assertFalse(marker.exists())

    def test_aliases_and_extension_types_rejected(self):
        self.write('plugin.yaml', 'name: &n demo\nother: *n\n')
        self.assertFalse(sv.validate_static(self.root)['ok'])
        (self.root / 'plugin.yaml').unlink()
        for extension in ([], None, 'bad', {'com.nousresearch.hermes': []}, {'com.nousresearch.hermes': {'servers': []}}):
            with self.subTest(extension=extension):
                self.write('plugin.json', json.dumps({'$schema': sv.PLUGIN_SCHEMA, 'name': 'demo', 'extensions': extension}))
                self.assertFalse(sv.validate_static(self.root)['ok'])


if __name__ == '__main__':
    unittest.main()
