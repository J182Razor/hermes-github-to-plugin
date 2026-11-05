import os
import sys
from pathlib import Path

PLUGIN_ROOT = Path("C:/Users/Lawa4721/scratchpad/hackathon/github-to-hermes")
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

# Avoid resolving `tools` to Hermes agent's tools package
for mod in ("tools", "schemas", "detector"):
    sys.modules.pop(mod, None)

import __init__ as plugin  # noqa: E402


class _Ctx:
    def __init__(self):
        self.tools = []

    def register_tool(self, name=None, **kwargs):
        self.tools.append(name)


def test_register_tools():
    ctx = _Ctx()
    plugin.register(ctx)
    assert set(ctx.tools) == {"detect_repo", "convert_repo", "validate_plugin"}
