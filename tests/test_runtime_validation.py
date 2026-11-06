import json
import os
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import tools
import schemas


class RuntimeValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ['TMPDIR'])
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'candidate'
        self.root.mkdir()
        (self.root / 'plugin.yaml').write_text('name: fixture\n')
        self.marker = Path(self.tmp.name) / 'marker'
        (self.root / '__init__.py').write_text('from pathlib import Path\nPath(' + repr(str(self.marker)) + ').write_text("ran")\ndef register(ctx): pass\n')

    def call(self, **kw):
        return json.loads(tools.handle_validate_plugin(dict(path=str(self.root), **kw)))

    def test_schema_and_invalid_mode(self):
        self.assertEqual(schemas.VALIDATE_PLUGIN_SCHEMA['parameters']['properties']['mode']['enum'], ['static', 'runtime'])
        self.assertFalse(self.call(mode='invalid')['success'])

    def test_static_does_not_execute_or_ask(self):
        with patch('subprocess.Popen', side_effect=AssertionError('unexpected child')):
            result = self.call()
        self.assertTrue(result['static_only'])
        self.assertFalse(self.marker.exists())

    def test_denied_never_launches(self):
        import sys
        import types
        approval = types.ModuleType('tools.approval')
        approval.request_tool_approval = lambda *a, **k: {'approved': False}
        with patch.dict(sys.modules, {'tools.approval': approval}), patch('subprocess.Popen', side_effect=AssertionError('unexpected child')):
            result = self.call(mode='runtime')
        self.assertFalse(result['success'])
        self.assertFalse(self.marker.exists())

class SnapshotTests(unittest.TestCase):
    def setUp(self):
        import runtime_validation as rv
        self.rv = rv
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ['TMPDIR'])
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'plugin'
        self.root.mkdir()
        (self.root / 'plugin.yaml').write_text('name: fixture\n')
        (self.root / '__init__.py').write_text('def register(ctx): pass\n')

    def gate(self, function):
        import sys, types
        approval = types.ModuleType('tools.approval')
        approval.request_tool_approval = function
        return patch.dict(sys.modules, {'tools.approval': approval})

    def test_denial_unavailable_and_tampering_do_not_start_child(self):
        for kind in ('denied', 'unavailable', 'tamper'):
            seen = []
            def answer(name, reason, **kwargs):
                seen.append((reason, kwargs))
                if kind == 'unavailable':
                    raise RuntimeError('secret-canary')
                if kind == 'tamper':
                    copies = list(Path(os.environ['TMPDIR']).glob('g2h_*/plugin/__init__.py'))
                    self.assertEqual(len(copies), 1)
                    copies[0].write_text('tampered')
                return {'approved': kind == 'tamper'}
            with self.subTest(kind=kind), self.gate(answer), patch('subprocess.Popen', side_effect=AssertionError('child started')):
                result = self.rv.validate_runtime(str(self.root), tools.detector)
            self.assertFalse(result['success'])
            self.assertEqual(len(seen), 1)
            self.assertIn('SHA256 snapshot:', seen[0][0])
            self.assertNotIn('secret-canary', str(result))
            if kind == 'tamper':
                self.assertIn('changed', result['error'])

    def test_bounded_and_hardlink_rejected(self):
        with patch.object(self.rv, 'MAX_BYTES', 1):
            with self.assertRaises(ValueError):
                self.rv.snapshot(self.root)
        with patch.object(self.rv, 'MAX_ENTRIES', 1):
            with self.assertRaises(ValueError):
                self.rv.snapshot(self.root)
        try:
            os.link(self.root / '__init__.py', self.root / 'duplicate')
        except OSError:
            self.skipTest('hardlinks unavailable')
        with self.assertRaises(ValueError):
            self.rv.snapshot(self.root)

    def test_timeout_is_sanitized(self):
        with self.gate(lambda *a, **kw: {'approved': True}), patch('subprocess.run', side_effect=subprocess.TimeoutExpired('secret-canary', 1)):
            result = self.rv.validate_runtime(str(self.root), tools.detector)
        self.assertFalse(result['success'])
        self.assertEqual(result['runtime_validation'], 'failed or incomplete')
        self.assertNotIn('secret-canary', str(result))

    def test_native_unsupported_doctor_is_not_success(self):
        import sys, types
        host = types.ModuleType('hermes_cli'); host.__file__ = __file__
        def run(args, **kwargs):
            Path(args[-2]).write_text(json.dumps({'validate_plugin_dir': 'passed', 'doctor_plugin': 'unsupported'}))
            return subprocess.CompletedProcess(args, 0)
        with self.gate(lambda *a, **kw: {'approved': True}), patch.dict(sys.modules, {'hermes_cli': host}), patch('subprocess.run', side_effect=run):
            result = self.rv.validate_runtime(str(self.root), tools.detector)
        self.assertFalse(result['success'])
        self.assertEqual(result['error_code'], 'worker_report_invalid')

    def test_cleanup_failure_preserves_completed_report(self):
        import sys, types
        host = types.ModuleType('hermes_cli'); host.__file__ = __file__
        def run(args, **kwargs):
            Path(args[-2]).write_text(json.dumps({'validate_plugin_dir': 'passed', 'doctor_plugin': 'passed'}))
            return subprocess.CompletedProcess(args, 0)
        real_cleanup = tools.detector._cleanup_clone
        def cleanup(path):
            real_cleanup(path)
            raise RuntimeError('secret-canary')
        with self.gate(lambda *a, **kw: {'approved': True}), patch.dict(sys.modules, {'hermes_cli': host}), patch('subprocess.run', side_effect=run), patch.object(tools.detector, '_cleanup_clone', side_effect=cleanup):
            result = self.rv.validate_runtime(str(self.root), tools.detector)
        self.assertFalse(result['success'])
        self.assertEqual(result['runtime_validation'], 'completed')
        self.assertEqual(result['checks']['doctor_plugin'], 'passed')
        self.assertIn('cleanup_warning', result)
        self.assertNotIn('secret-canary', str(result))

    def test_missing_and_ambiguous_diagnostics(self):
        missing = self.rv.validate_runtime(str(self.root / 'missing'), tools.detector)
        (self.root / 'plugin.json').write_text('{}')
        ambiguous = self.rv.validate_runtime(str(self.root), tools.detector)
        self.assertEqual(missing['error_code'], 'invalid_path')
        self.assertEqual(ambiguous['error_code'], 'ambiguous_manifest')
        self.assertNotEqual(missing['error'], ambiguous['error'])

    def test_worker_keeps_native_kind_when_manifest_removed(self):
        import sys
        host = Path(self.tmp.name) / 'fakehost'
        package = host / 'hermes_cli'; package.mkdir(parents=True)
        (package / '__init__.py').write_text('')
        (package / 'plugin_validate.py').write_text(
            'from types import SimpleNamespace\n'
            'def validate_plugin_dir(root):\n'
            '    (root / "plugin.yaml").unlink()\n'
            '    return SimpleNamespace(ok=True)\n')
        (package / 'plugin_dev.py').write_text(
            'from types import SimpleNamespace\n'
            'def doctor_plugin(root):\n'
            '    return SimpleNamespace(ok=False, findings=[SimpleNamespace(level="error", '
            'message="hook callback SECRET_CANARY for X must accept **kwargs for forward compatibility")])\n')
        output = Path(self.tmp.name) / 'report.json'
        proc = subprocess.run([sys.executable, '-B', '-c', self.rv._WORKER, str(host), str(self.root), str(output), 'native'], capture_output=True, timeout=20)
        self.assertEqual(proc.returncode, 0)
        report = json.loads(output.read_text())
        self.assertEqual(report['doctor_plugin'], 'failed')
        self.assertEqual(report['reason'], 'hook_requires_kwargs')
        self.assertNotIn('SECRET_CANARY', str(report))

    def test_doctor_reason_is_allowlisted_and_static(self):
        import sys, types
        host = types.ModuleType('hermes_cli'); host.__file__ = __file__
        def run(args, **kwargs):
            Path(args[-2]).write_text(json.dumps({'validate_plugin_dir': 'passed', 'doctor_plugin': 'failed', 'reason': 'hook_requires_kwargs', 'untrusted': 'SECRET_CANARY'}))
            return subprocess.CompletedProcess(args, 0)
        with self.gate(lambda *a, **kw: {'approved': True}), patch.dict(sys.modules, {'hermes_cli': host}), patch('subprocess.run', side_effect=run):
            result = self.rv.validate_runtime(str(self.root), tools.detector)
        self.assertEqual(result['error_code'], 'doctor_failed')
        self.assertEqual(result['reason_code'], 'hook_requires_kwargs')
        self.assertIn('**kwargs', result['error'])
        self.assertNotIn('SECRET_CANARY', str(result))

    def test_unknown_exception_recommends_local_review(self):
        with patch.object(tools.detector, 'reject_link_components', side_effect=RuntimeError('SECRET_CANARY')):
            result = self.rv.validate_runtime(str(self.root), tools.detector)
        self.assertEqual(result['error_code'], 'unknown')
        self.assertIn('local operator review', result['error'])
        self.assertNotIn('SECRET_CANARY', str(result))

    def test_missing_host_is_sanitized(self):
        with self.gate(lambda *a, **kw: {'approved': True}), patch.dict('sys.modules', {'hermes_cli': None}), patch('subprocess.Popen', side_effect=AssertionError('child started')):
            result = self.rv.validate_runtime(str(self.root), tools.detector)
        self.assertFalse(result['success'])
        self.assertEqual(result['runtime_validation'], 'not run')
