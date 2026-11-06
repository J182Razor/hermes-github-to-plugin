import builtins
import json
import tempfile
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('release_tools', ROOT / 'tools.py')
tools = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tools)
detector = tools.detector


class ReleaseReviewTests(unittest.TestCase):
    def test_default_names_are_stable_safe_and_distinct(self):
        name = detector.default_plugin_name
        self.assertEqual(name('https://github.com/Owner/Repo.git'), name('https://github.com/owner/repo'))
        pairs = [('https://github.com/psf/requests', 'https://github.com/pallets/flask'),
                 ('https://github.com/owner/a_b', 'https://github.com/owner/a-b'),
                 ('https://github.com/owner/' + 'a' * 200 + 'x', 'https://github.com/owner/' + 'a' * 200 + 'y')]
        for a, b in pairs:
            self.assertNotEqual(name(a), name(b))
            self.assertLessEqual(len(name(a)), 64)
        self.assertTrue(name('https://github.com/con/nul').startswith('g2h-'))

    def test_two_conversions_and_duplicate_do_not_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def generate(source, out, name, rep):
                dest = Path(out) / name
                dest.mkdir()
                (dest / 'content').write_text(rep['repo_url'])
                return {'output_path': str(dest)}
            def fake_clone(url):
                return str(root)
            generator = SimpleNamespace(generate=generate)
            with patch.object(detector, 'clone_repo', side_effect=fake_clone), \
                 patch.object(detector, '_cleanup_clone'), \
                 patch.object(detector, 'detect_type', return_value={'type': 'skill_bundle'}), \
                 patch.object(tools, '_load_module', return_value=generator):
                def convert(url):
                    return json.loads(tools.handle_convert_repo({'repo_url': url, 'output_dir': tmp}))
                first = convert('https://github.com/psf/requests')
                second = convert('https://github.com/pallets/flask')
                self.assertTrue(first['success'])
                self.assertTrue(second['success'])
                self.assertNotEqual(first['output_path'], second['output_path'])
                file = Path(first['output_path']) / 'content'
                before = file.read_bytes()
                self.assertFalse(convert('https://github.com/psf/requests')['success'])
                self.assertEqual(file.read_bytes(), before)

    def test_diagnostics_do_not_echo_credentials(self):
        marker = 'SECRET_SENTINEL'
        for handler in (tools.handle_detect_repo, tools.handle_convert_repo):
            result = handler({'repo_url': 'https://' + marker + '@github.com/owner/repo'})
            self.assertNotIn(marker, result)
            self.assertIn('invalid repository URL', result)
            for exc, expected in [(detector.WindowsACLSupportError(marker), 'pywin32'),
                                  (detector.CloneAccessError(marker), 'network')]:
                with patch.object(detector, 'detect_repo', side_effect=exc):
                    result = handler({'repo_url': 'https://github.com/owner/repo'})
                    self.assertIn(expected, result)
                    self.assertNotIn(marker, result)

    def test_missing_windows_import_and_clone_failure_cleanup(self):
        original = builtins.__import__
        def import_missing(name, *args, **kwargs):
            if name == 'pywintypes':
                raise ImportError('SECRET_SENTINEL')
            return original(name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=import_missing):
            with self.assertRaises(detector.WindowsACLSupportError):
                detector._windows_private_clone_dir(Path('.'))
        with patch.object(detector, '_run', return_value=(1, '', 'SECRET_SENTINEL')), \
             patch.object(detector, '_cleanup_clone') as cleanup, \
             patch.object(detector, '_windows_private_clone_dir', return_value='synthetic-clone'):
            # Windows host: private-directory creation is isolated from the test.
            result = tools.handle_detect_repo({'repo_url': 'https://github.com/owner/repo'})
            self.assertIn('network', result)
            self.assertNotIn('SECRET_SENTINEL', result)
            cleanup.assert_called_once()

    def test_manifest_and_documented_dependencies(self):
        manifest = (ROOT / 'plugin.yaml').read_text()
        readme = (ROOT / 'README.md').read_text()
        deps = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['dependencies']
        self.assertNotIn('github_token:', manifest)
        self.assertNotIn('output_root:', manifest)
        self.assertNotIn('Convert any', manifest)
        self.assertTrue(any(x.startswith('PyYAML') for x in deps))
        self.assertTrue(any(x.startswith('pywin32') and 'win32' in x for x in deps))
        for term in ('PyYAML', 'pywin32', 'never installs', 'explicit `type=mcp`', 'not installed'):
            self.assertIn(term, readme)

    def test_yaml_conversion_reports_missing_dependency_without_raw_details(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source'; source.mkdir()
            (source / 'openapi.yaml').write_text('openapi: 3.0.0\npaths: {}\n')
            original = builtins.__import__
            def missing_yaml(name, *args, **kwargs):
                if name == 'yaml':
                    raise ImportError('SECRET_SENTINEL')
                return original(name, *args, **kwargs)
            with patch('builtins.__import__', side_effect=missing_yaml):
                result = json.loads(tools._convert_detected(
                    {'repo_path': str(source), 'type': 'rest'}, None, 'test-plugin', str(root / 'out')))
            self.assertFalse(result['success'])
            self.assertIn('PyYAML', result['error'])
            self.assertNotIn('SECRET_SENTINEL', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
