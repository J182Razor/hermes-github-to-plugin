from __future__ import annotations

import json
from pathlib import Path


def generate(repo_path: str, out_dir: str, name: str, report: dict) -> dict:
    out = Path(out_dir) / name
    out.mkdir(parents=True, exist_ok=True)

    manifest = f"""name: {name}
version: 1.0.0
description: "CLI wrapper plugin generated from {report.get('repo_url', '')}"
author: BlueStarJL
license: MIT
homepage: {report.get('repo_url', '')}
tags: [tools, cli, generated]
requires_hermes: ">=0.22.0"
provides_tools:
  - run_cli
provides_hooks: []
requires_env: []
config_schema:
  cli_path: {{type: str, default: "", label: "Path to CLI executable"}}
python_dependencies: []
"""
    (out / "plugin.yaml").write_text(manifest, encoding="utf-8")

    (out / "schemas.py").write_text(
        '''from __future__ import annotations

RUN_CLI_SCHEMA = {
    "name": "run_cli",
    "description": "Run an allowlisted subcommand of the wrapped CLI.",
    "parameters": {
        "type": "object",
        "properties": {
            "subcommand": {"type": "string", "description": "Subcommand to run."},
            "args": {"type": "array", "items": {"type": "string"}, "description": "Arguments."},
        },
        "required": ["subcommand"],
        "additionalProperties": False,
    },
}
''',
        encoding="utf-8",
    )

    (out / "tools.py").write_text(
        '''from __future__ import annotations

import json
import subprocess
from typing import Any, Dict


ALLOWED = {"--version", "help", "status"}


def handle_run_cli(args: Dict[str, Any], **_kw) -> str:
    sub = args.get("subcommand") or ""
    extra = args.get("args") or []
    if sub not in ALLOWED and not sub.isalnum():
        return json.dumps({"success": False, "error": "subcommand not allowed"}, ensure_ascii=False)
    cmd = ["cli", sub] + [str(x) for x in extra]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return json.dumps({"success": r.returncode == 0, "stdout": r.stdout, "stderr": r.stderr}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)
''',
        encoding="utf-8",
    )

    (out / "__init__.py").write_text(
        '''from __future__ import annotations

from .schemas import RUN_CLI_SCHEMA
from .tools import handle_run_cli


def register(ctx) -> None:
    ctx.register_tool(name="run_cli", toolset="''' + name + '''", schema=RUN_CLI_SCHEMA, handler=handle_run_cli)
''',
        encoding="utf-8",
    )
    (out / "README.md").write_text(f"# {name}\n\nCLI wrapper (discloses subprocess usage).\n", encoding="utf-8")
    return {"output_path": str(out), "files": sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file())}
