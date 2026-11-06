from __future__ import annotations

import json
from typing import Any, Dict


def _schema(name: str, description: str, properties: Dict[str, Any], required=None) -> Dict[str, Any]:
    params: Dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        params["required"] = required
    params["additionalProperties"] = False
    return {"name": name, "description": description, "parameters": params}


def _str(description: str) -> Dict[str, Any]:
    return {"type": "string", "description": description}


DETECT_REPO_SCHEMA = _schema(
    "detect_repo",
    "Clone (shallow) a GitHub repository and detect what it exposes: MCP server, REST/OpenAPI, CLI, Python package, or skill collection. Returns a JSON report with type, evidence, suggested name, entrypoints, env vars and endpoints.",
    {"repo_url": _str("GitHub repository URL (https://github.com/owner/repo).")},
    required=["repo_url"],
)

CONVERT_REPO_SCHEMA = _schema(
    "convert_repo",
    "Convert supported repository types without running validation or source code. Review generated output before enabling; unsupported types fail closed.",
    {
        "repo_url": _str("GitHub repository URL."),
        "output_dir": _str("Directory where the generated plugin is written. Defaults to a temp dir."),
        "name": _str("Override the generated plugin name."),
        "type": _str("Force a detection type: mcp, rest, cli, python_pkg, skill_bundle, unknown."),
    },
    required=["repo_url"],
)

VALIDATE_PLUGIN_SCHEMA = _schema(
    "validate_plugin",
    "Static metadata checks by default. Runtime mode executes plugin code with your OS privileges, without a sandbox; standard Hermes approval policy may bypass prompts.",
    {"path": _str("Path to the plugin directory."),
     "mode": {"type": "string", "enum": ["static", "runtime"], "description": "Runtime invokes the normal Hermes approval gate; it may not prompt."}},
    required=["path"],
)
