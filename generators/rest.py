from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


def generate(repo_path: str, out_dir: str, name: str, report: dict) -> dict:
    root = Path(repo_path)
    out = Path(out_dir) / name
    out.mkdir(parents=True, exist_ok=True)

    # plugin.yaml
    env_var = (report.get("env_vars") or [f"{name.upper().replace('-', '_')}_API_KEY"])[0]
    manifest = f"""name: {name}
version: 1.0.0
description: "REST API plugin generated from {report.get('repo_url', '')}"
author: BlueStarJL
license: MIT
homepage: {report.get('repo_url', '')}
tags: [tools, rest, generated]
requires_hermes: ">=0.22.0"
provides_tools:
  - api_request
provides_hooks: []
requires_env:
  - name: {env_var}
    description: "API credential"
    url: "{report.get('repo_url', '')}"
    secret: true
config_schema:
  base_url: {{type: str, default: "", label: "API base URL"}}
  timeout: {{type: int, default: 30}}
python_dependencies:
  - "httpx>=0.27,<1"
"""
    (out / "plugin.yaml").write_text(manifest, encoding="utf-8")

    # schemas.py
    (out / "schemas.py").write_text(
        '''from __future__ import annotations


API_REQUEST_SCHEMA = {
    "name": "api_request",
    "description": "Make an authenticated request to the configured REST API.",
    "parameters": {
        "type": "object",
        "properties": {
            "method": {"type": "string", "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"]},
            "path": {"type": "string", "description": "API path (e.g. /users)."},
            "body": {"type": "object", "description": "JSON body for write methods."},
        },
        "required": ["method", "path"],
        "additionalProperties": False,
    },
}
''',
        encoding="utf-8",
    )

    # tools.py
    (out / "tools.py").write_text(
        '''from __future__ import annotations

import json
from typing import Any, Dict

import httpx

from agent.secret_scope import get_secret


def _config():
    try:
        import os
        return os.environ.get("BASE_URL", "")
    except Exception:
        return ""


def handle_api_request(args: Dict[str, Any], **_kw) -> str:
    method = (args.get("method") or "GET").upper()
    path = args.get("path") or ""
    body = args.get("body")
    token = get_secret("''' + env_var + '''")
    base_url = _config()
    url = base_url.rstrip("/") + "/" + path.lstrip("/")
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.request(method, url, json=body, headers=headers)
        try:
            data = resp.json()
        except Exception:
            data = resp.text
        return json.dumps({"success": resp.is_success, "status": resp.status_code, "data": data}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)
''',
        encoding="utf-8",
    )

    # __init__.py
    (out / "__init__.py").write_text(
        '''from __future__ import annotations

from .schemas import API_REQUEST_SCHEMA
from .tools import handle_api_request


def register(ctx) -> None:
    ctx.register_tool(
        name="api_request",
        toolset="''' + name + '''",
        schema=API_REQUEST_SCHEMA,
        handler=handle_api_request,
    )
''',
        encoding="utf-8",
    )

    (out / "README.md").write_text(
        f"# {name}\n\nGenerated REST plugin.\n\nSource: {report.get('repo_url', '')}\n\n"
        "## Disclosure\n\nNetwork calls to configured base URL. Credential via Hermes secret scope.\n",
        encoding="utf-8",
    )
    return {"output_path": str(out), "files": sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file())}
