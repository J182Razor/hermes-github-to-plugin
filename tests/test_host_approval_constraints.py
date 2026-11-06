from host_test_config import host_test_config
"""Executable checks for host semantics that prevent a safe hook-only adapter."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class HostApprovalConstraints(unittest.TestCase):
    def test_real_dispatcher_and_gate_do_not_guarantee_source_specific_human_consent(self):
        host, host_python = host_test_config(windows_only=False)

        code = r'''
from unittest.mock import patch
from hermes_cli import plugins, lifecycle
from tools import approval

# The first approval directive wins even when a later one binds the source digest.
directives = [dict(action='approve', message='unrelated', rule_key='other'),
              dict(action='approve', message='source sha256:abc', rule_key='tool:abc')]
with patch.object(lifecycle, 'invoke_hook', return_value=directives), \
     patch.object(approval, 'request_tool_approval', return_value={'approved': True}) as gate:
    blocked, _ = plugins._dispatch_pre_tool_call_hooks('fixture', {})
    assert blocked is None
    assert gate.call_args.kwargs['rule_key'] == 'other'

# The real gate authorizes without a human when policy is off.
with patch.object(approval, '_yolo_active', return_value=False), \
     patch.object(approval.approval_context, '_get_approval_mode', return_value='off'):
    result = approval.request_tool_approval('fixture', 'source sha256:abc', rule_key='tool:abc')
    assert result['approved'] is True

# Hook dispatcher failure is deliberately fail-open in the actual execution helper.
from agent.tool_executor import _pre_tool_block
class Ref:
    name='fixture'; args={}; task_id=''; call_id=''; trace=[]
class Agent: pass
with patch.object(plugins, '_dispatch_pre_tool_call_hooks', side_effect=RuntimeError('failure')):
    from unittest.mock import patch as p
    with p('agent.tool_executor.tool_hook_ids', return_value={}):
        blocked, args = _pre_tool_block(Agent(), Ref())
        assert blocked is None and args == {}
'''
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            env = dict(os.environ, HERMES_HOME=tmp, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH='')
            proc = subprocess.run([str(host_python), '-c', code], cwd=str(host), env=env,
                                  capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
