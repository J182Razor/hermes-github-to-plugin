from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict

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

detector = _load_module("detector", "detector.py")


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _detection_error(exc):
    if isinstance(exc, detector.InvalidRepoURL):
        return 'invalid repository URL; expected credential-free GitHub HTTPS owner/repo'
    if isinstance(exc, detector.WindowsACLSupportError):
        return 'Windows private ACL support unavailable; install pywin32 in the converter environment'
    if isinstance(exc, detector.CloneAccessError):
        return 'repository inaccessible or network clone failed'
    return 'repository detection failed'


def handle_detect_repo(args: Dict[str, Any], **_kw) -> str:
    url = (args.get("repo_url") or "").strip()
    if not url:
        return _json({"success": False, "error": "repo_url required"})
    try:
        rep = detector.detect_repo(url)
        cleanup = rep.pop('_cleanup', None)
        try:
            rep.pop('repo_path', None)
            return _json({"success": True, **rep})
        finally:
            if cleanup:
                cleanup()
    except Exception as exc:
        return _json({"success": False, "error": _detection_error(exc)})


def _resolve_output_dir(output_dir: str) -> str:
    if output_dir:
        return output_dir
    # config not easily available; use scratchpad
    return os.path.join(tempfile.gettempdir(), "g2h_output")


def handle_convert_repo(args: Dict[str, Any], **_kw) -> str:
    url = (args.get("repo_url") or "").strip()
    if not url:
        return _json({"success": False, "error": "repo_url required"})
    name = (args.get("name") or "").strip()
    if len(name) > 64:
        return _json({"success": False, "error": "invalid plugin name: maximum 64 characters"})
    if name and (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name)
                 or name.endswith(".")
                 or re.fullmatch(r"(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])", name.split(".")[0])):
        return _json({"success": False, "error": "invalid plugin name"})
    forced_type = (args.get("type") or "").strip()
    output_dir = _resolve_output_dir((args.get("output_dir") or "").strip())

    try:
        rep = detector.detect_repo(url, retain_clone=True)
    except Exception as exc:
        return _json({"success": False, "error": _detection_error(exc)})

    cleanup = rep.pop('_cleanup', None)
    try:
        result = _convert_detected(rep, forced_type, name, output_dir)
    finally:
        if cleanup:
            try:
                cleanup()
            except Exception:
                return _json({'success': False, 'error': 'clone cleanup failed; inspect private scratch directory'})
    return result


def _convert_detected(rep, forced_type, name, output_dir):
    if len(name) > 64:
        return _json({"success": False, "error": "invalid plugin name: maximum 64 characters"})
    if forced_type:
        rep["type"] = forced_type
    rep['_explicit_mcp'] = forced_type == 'mcp'
    if not name:
        name = rep.get("suggested_name", "converted-plugin").replace(" ", "-").lower()
        # sanitize
        name = re.sub(r"[^a-z0-9-]", "-", name).strip("-") or "converted-plugin"

    repo_path = rep["repo_path"]
    ptype = rep["type"]
    # Do not silently turn an MCP-dependent console server into a CLI adapter.
    # Explicit CLI remains available when the operator intends that behavior.
    if not forced_type and ptype == 'cli':
        try:
            metadata = detector._metadata_io.toml_data(Path(repo_path) / 'pyproject.toml', detector.METADATA_LIMIT)
            project = metadata.get('project', {})
            dependencies = project.get('dependencies', []) if isinstance(project, dict) else []
            if isinstance(dependencies, list) and any(
                isinstance(req, str) and re.match(r'(?i)^mcp(?:[ \t]*(?:\[|==|!=|<=|>=|<|>|;|@)|$)', req)
                for req in dependencies
            ):
                return _json({'success': False, 'error': 'MCP dependency with CLI detection is ambiguous; inspect the source and explicitly select type mcp or cli'})
        except (OSError, ValueError):
            pass
    try:
        detector.reject_link_components(Path(output_dir) / name)
    except (OSError, ValueError) as e:
        return _json({'success': False, 'error': 'unsafe or inaccessible output path'})
    if (Path(output_dir) / name).exists() or (Path(output_dir) / name).is_symlink():
        return _json({"success": False, "error": "output path already exists"})

    try:
        if ptype in ("skill_bundle", "rest", "mcp"):
            filename = ptype + "_safe" if ptype in ("rest", "mcp") else ptype
            gen = _load_module(f"generator_{ptype}", f"generators/{filename}.py")
        elif ptype in ("cli", "python_pkg"):
            gen = _load_module("generator_fixed_adapter", "generators/fixed_adapter.py")
        else:
            return _json({"success": False, "error": "unsupported or ambiguous source; inspect it and specify type=mcp or type=cli if appropriate", "type": ptype})
        gen_result = gen.generate(repo_path, output_dir, name, rep)
    except ValueError as exc:
        if ptype == 'rest' and str(exc) == 'YAML support requires PyYAML':
            return _json({"success": False, "error": "YAML OpenAPI conversion requires PyYAML in the converter environment"})
        if ptype == 'mcp':
            return _json({"success": False, "error": "MCP source is unsupported or ambiguous; inspect source and specify type=mcp for the supported console subset, or provide a supported mcp.json"})
        return _json({"success": False, "error": "generation failed; source may be unsupported or output inaccessible"})
    except Exception:
        return _json({"success": False, "error": "generation failed; source may be unsupported or output inaccessible"})

    return _json({"success": True, "output_path": gen_result["output_path"],
                  "type": ptype, "status": gen_result.get("status", "generated; review required"), "validation": "not run; review required",
                  "files": gen_result.get("files", [])})


def handle_validate_plugin(args: Dict[str, Any], **_kw) -> str:
    mode = args.get("mode", "static")
    if mode not in ("static", "runtime"):
        return _json({"success": False, "error": "invalid validation mode"})
    path = args.get("path")
    if not isinstance(path, str) or not path.strip():
        return _json({"success": False, "static_only": True, "runtime_validation": "not run",
                      "errors": ["path required"], "checks": [],
                      "warnings": ["Static checks are not full Hermes validation or a security assessment."]})
    if mode == "runtime":
        try:
            runtime = _load_module("runtime_validation", "runtime_validation.py")
            return _json(runtime.validate_runtime(path.strip(), detector))
        except Exception:
            return _json({"success": False, "runtime_validation": "not run", "error": "runtime validation unavailable"})
    try:
        validator = _load_module("static_validation", "static_validation.py")
        report = validator.validate_static(path.strip())
        report["success"] = report.pop("ok")
        return _json(report)
    except Exception:
        return _json({"success": False, "static_only": True, "runtime_validation": "not run",
                      "checks": [], "errors": ["static validation could not be completed"],
                      "warnings": ["Static checks are not full Hermes validation or a security assessment."]})
