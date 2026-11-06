"""Real host registration/dispatch with simulated approval, never a human-consent claim."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from host_test_config import host_test_config


class RuntimeHostTests(unittest.TestCase):
    def test_host_dispatch(self):
        host, python = host_test_config()
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp)
            candidate = root / 'fixture'; candidate.mkdir()
            marker = root / 'marker'
            (candidate / 'plugin.yaml').write_text('name: fixture\nversion: 1.0.0\ndescription: Fixture\nprovides_tools: []\nprovides_hooks: []\n')
            (candidate / '__init__.py').write_text('import os\nfrom pathlib import Path\nassert not os.environ.get("G2H_TEST_SECRET")\nPath(' + repr(str(marker)) + ').write_text("ran")\ndef register(ctx): pass\nprint("SECRET_CANARY_SHOULD_NOT_APPEAR")\n')
            code = r'''
import json, os, sys
from pathlib import Path
from unittest.mock import patch
from hermes_cli.plugins import PluginManager
from tools import approval
from tools.terminal_tool import set_approval_callback
from model_tools import handle_function_call
repo, candidate, marker = map(Path, sys.argv[1:])
manager = PluginManager()
manifest = next(m for m in manager._scan_directory(repo.parent, source='user') if m.name == 'github-to-hermes' and Path(m.path).resolve() == repo.resolve())
manager._load_plugin(manifest)
loaded = manager._plugins.get(manifest.key or manifest.name)
assert loaded and not loaded.error, loaded
os.environ['HERMES_INTERACTIVE'] = '1'
os.environ.pop('HERMES_SINGLE_QUERY_SESSION', None)
answers = []
def no(command, description, **kw):
    answers.append(command); return 'n'
set_approval_callback(no)
with patch.object(approval, '_yolo_active', return_value=False), \
     patch.object(approval.approval_context, '_get_approval_mode', return_value='manual'), \
     patch.object(approval, '_presence', return_value=(None, False, False, False)), \
     patch.object(approval, '_unattended_contexts', return_value=[]):
    unattended = json.loads(handle_function_call('validate_plugin', {'path': str(candidate), 'mode': 'runtime'}))
    assert not unattended['success'] and not marker.exists(), unattended
with patch.object(approval, '_yolo_active', return_value=False), \
     patch.object(approval.approval_context, '_get_approval_mode', return_value='manual'), \
     patch.object(approval, '_presence', return_value=(None, True, False, False)), \
     patch.object(approval, '_human_decision', side_effect=[{'approved': False}, {'approved': True}]), \
     patch.object(approval, 'request_tool_approval', wraps=approval.request_tool_approval) as gate:
    denied = json.loads(handle_function_call('validate_plugin', {'path': str(candidate), 'mode': 'runtime'}))
    assert not denied['success'] and not marker.exists(), denied
    assert gate.call_count == 1 and 'SHA256 snapshot:' in gate.call_args.args[1]
    def yes(command, description, **kw):
        answers.append(command); return 'once'
    set_approval_callback(yes)
    result = json.loads(handle_function_call('validate_plugin', {'path': str(candidate), 'mode': 'runtime'}))
    assert gate.call_count == 2
    assert gate.call_args.kwargs['rule_key'] != gate.call_args_list[0].kwargs['rule_key']
    assert marker.exists(), result
    assert result['checks']['validate_plugin_dir'] == 'passed', result
    assert result['checks']['doctor_plugin'] == 'passed', result
    assert result['success'], result
    assert 'SECRET_CANARY_SHOULD_NOT_APPEAR' not in json.dumps(result)
    portable = candidate.parent / 'portable'; portable.mkdir()
    (portable / 'plugin.json').write_text(json.dumps({'$schema': 'https://agent-plugins.org/schemas/1.0.0/plugin.schema.json', 'name': 'portable-fixture'}))
    with patch.object(approval, '_human_decision', return_value={'approved': True}):
        portable_result = json.loads(handle_function_call('validate_plugin', {'path': str(portable), 'mode': 'runtime'}))
    assert portable_result['checks']['doctor_plugin'] == 'unsupported', portable_result
    assert portable_result['success'] == (portable_result['checks']['validate_plugin_dir'] == 'passed'), portable_result
set_approval_callback(None)
manager.unload()
print('HOST_RUNTIME_OK simulated denial and approval; host validation and doctor passed')
'''
            env = dict(os.environ, HERMES_HOME=str(root / 'home'), TMPDIR=tmp, TMP=tmp, TEMP=tmp,
                       PYTHONDONTWRITEBYTECODE='1', PYTHONPATH='', G2H_TEST_SECRET='synthetic-canary')
            for key in ('HERMES_SINGLE_QUERY_SESSION', 'HERMES_GATEWAY_SESSION', 'HERMES_SESSION_PLATFORM', 'HERMES_CRON_SESSION', 'HERMES_YOLO_MODE'):
                env.pop(key, None)
            proc = subprocess.run([str(python), '-B', '-c', code, str(repo), str(candidate), str(marker)],
                                  cwd=str(host), env=env, capture_output=True, text=True, timeout=150)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn('HOST_RUNTIME_OK', proc.stdout)
