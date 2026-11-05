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
    "Detect a GitHub repository's type and generate a complete Hermes plugin (native or portable Agent Plugin v1) into output_dir/<name>/. Runs validation on the result.",
    {
        "repo_url": _str("GitHub repository URL."),
        "output_dir": _str("Directory where the generated plugin is written. Defaults to the plugin's output_root config or a temp dir."),
        "name": _str("Override the generated plugin name."),
        "type": _str("Force a detection type: mcp, rest, cli, python_pkg, skill_bundle, unknown."),
    },
    required=["repo_url"],
)

VALIDATE_PLUGIN_SCHEMA = _schema(
    "validate_plugin",
    "Run `hermes plugins validate <path> --install-deps` and `hermes plugins doctor <path> --ci` on a plugin directory and return a combined report.",
    {"path": _str("Path to the plugin directory.")},
    required=["path"],
)
