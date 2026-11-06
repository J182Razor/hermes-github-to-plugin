"""Prerequisites for optional, explicitly enforceable real-host integration tests."""
import os
from pathlib import Path
import subprocess
import unittest


def host_test_config(windows_only=False):
    errors = []
    root_value = os.environ.get('HERMES_SOURCE_ROOT', '')
    python_value = os.environ.get('HERMES_TEST_PYTHON', '')
    root = Path(root_value) if root_value else None
    python = Path(python_value) if python_value else None
    if not root or not root.is_dir() or not (root / 'model_tools.py').is_file() or not (root / 'hermes_cli' / 'agent_plugins.py').is_file():
        errors.append('HERMES_SOURCE_ROOT must point to a Hermes source checkout')
    if not python or not python.is_file():
        errors.append('HERMES_TEST_PYTHON must point to the Hermes Python interpreter')
    else:
        try:
            probe = subprocess.run([str(python), '-c', 'import sys; assert sys.version_info >= (3, 11)'], env=dict(os.environ, PYTHONPATH=''), capture_output=True, timeout=15)
            if probe.returncode:
                errors.append('HERMES_TEST_PYTHON must be a working Python 3.11+ interpreter')
        except (OSError, subprocess.SubprocessError):
            errors.append('HERMES_TEST_PYTHON must be a working Python 3.11+ interpreter')
    if windows_only and os.name != 'nt':
        errors.append('this integration requires Windows process containment')
    if errors:
        message = '; '.join(errors)
        if os.environ.get('HERMES_REQUIRE_HOST_TESTS') == '1':
            raise RuntimeError(message)
        raise unittest.SkipTest(message)
    return root.resolve(), python.absolute()
