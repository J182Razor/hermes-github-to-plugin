from __future__ import annotations

from pathlib import Path


def generate(repo_path: str, out_dir: str, name: str, report: dict) -> dict:
    out = Path(out_dir) / name
    out.mkdir(parents=True, exist_ok=True)

    pkg = name.replace("-", "_")
    manifest = f"""name: {name}
version: 1.0.0
description: "Python package wrapper generated from {report.get('repo_url', '')}"
author: BlueStarJL
license: MIT
homepage: {report.get('repo_url', '')}
tags: [tools, python, generated]
requires_hermes: ">=0.22.0"
provides_tools:
  - call_package
provides_hooks: []
requires_env: []
python_dependencies:
  - "{pkg}>=0.1"
"""
    (out / "plugin.yaml").write_text(manifest, encoding="utf-8")

    (out / "schemas.py").write_text(
        '''from __future__ import annotations

CALL_PACKAGE_SCHEMA = {
    "name": "call_package",
    "description": "Call a function from the wrapped Python package.",
    "parameters": {
        "type": "object",
        "properties": {
            "function": {"type": "string", "description": "Fully qualified function name."},
            "args": {"type": "array", "items": {}, "description": "Positional args."},
        },
        "required": ["function"],
        "additionalProperties": False,
    },
}
''',
        encoding="utf-8",
    )

    (out / "tools.py").write_text(
        '''from __future__ import annotations

import importlib
import json
from typing import Any, Dict


def handle_call_package(args: Dict[str, Any], **_kw) -> str:
    fn_name = args.get("function") or ""
    try:
        mod_name, attr = fn_name.rsplit(".", 1)
        mod = importlib.import_module(mod_name)
        fn = getattr(mod, attr)
        result = fn(*(args.get("args") or []))
        return json.dumps({"success": True, "result": repr(result)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)
''',
        encoding="utf-8",
    )

    (out / "__init__.py").write_text(
        '''from __future__ import annotations

from .schemas import CALL_PACKAGE_SCHEMA
from .tools import handle_call_package


def register(ctx) -> None:
    ctx.register_tool(name="call_package", toolset="''' + name + '''", schema=CALL_PACKAGE_SCHEMA, handler=handle_call_package)
''',
        encoding="utf-8",
    )
    (out / "README.md").write_text(f"# {name}\n\nPython package wrapper.\n", encoding="utf-8")
    return {"output_path": str(out), "files": sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file())}
