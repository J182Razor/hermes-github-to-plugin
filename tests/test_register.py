import subprocess
import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]

class RegistrationTests(unittest.TestCase):
    def test_register_tools(self):
        # Keep plugin imports isolated from the parent test suite's modules.
        script = '''
import importlib.util
import sys
from pathlib import Path

root = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("_g2h_registration_test", root / "__init__.py")
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)

class Ctx:
    def __init__(self):
        self.tools = []
    def register_tool(self, name=None, **kwargs):
        self.tools.append(name)

ctx = Ctx()
plugin.register(ctx)
assert set(ctx.tools) == {"detect_repo", "convert_repo", "validate_plugin"}, ctx.tools
'''
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", script, str(PLUGIN_ROOT)],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
