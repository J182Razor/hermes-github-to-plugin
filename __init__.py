from __future__ import annotations

import os
import sys
import importlib.util

_HERE = os.path.dirname(os.path.abspath(__file__))

def _load_module(name, filename):
    path = os.path.join(_HERE, filename)
    spec = importlib.util.spec_from_file_location(f"_g2h_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

schemas = _load_module("schemas", "schemas.py")
tools = _load_module("tools", "tools.py")

DETECT_REPO_SCHEMA = schemas.DETECT_REPO_SCHEMA
CONVERT_REPO_SCHEMA = schemas.CONVERT_REPO_SCHEMA
VALIDATE_PLUGIN_SCHEMA = schemas.VALIDATE_PLUGIN_SCHEMA

handle_detect_repo = tools.handle_detect_repo
handle_convert_repo = tools.handle_convert_repo
handle_validate_plugin = tools.handle_validate_plugin

_TOOLS = (
    ("detect_repo", DETECT_REPO_SCHEMA, handle_detect_repo, "🔍"),
    ("convert_repo", CONVERT_REPO_SCHEMA, handle_convert_repo, "🛠️"),
    ("validate_plugin", VALIDATE_PLUGIN_SCHEMA, handle_validate_plugin, "✅"),
)


def register(ctx) -> None:
    for name, schema, handler, emoji in _TOOLS:
        ctx.register_tool(name=name, toolset="github_to_hermes", schema=schema, handler=handler, emoji=emoji)
