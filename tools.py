from __future__ import annotations

import json
import os
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


def handle_detect_repo(args: Dict[str, Any], **_kw) -> str:
    url = (args.get("repo_url") or "").strip()
    if not url:
        return _json({"success": False, "error": "repo_url required"})
    try:
        rep = detector.detect_repo(url)
        return _json({"success": True, **rep})
    except Exception as e:
        return _json({"success": False, "error": str(e)})


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
    forced_type = (args.get("type") or "").strip()
    output_dir = _resolve_output_dir((args.get("output_dir") or "").strip())

    try:
        rep = detector.detect_repo(url)
    except Exception as e:
        return _json({"success": False, "error": str(e)})

    if forced_type:
        rep["type"] = forced_type
    if not name:
        name = rep.get("suggested_name", "converted-plugin").replace(" ", "-").lower()
        # sanitize
        import re
        name = re.sub(r"[^a-z0-9-]", "-", name).strip("-") or "converted-plugin"

    repo_path = rep["repo_path"]
    ptype = rep["type"]

    try:
        if ptype == "mcp":
            try:
                from generators import mcp as gen
            except Exception:
                import sys, os
                sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                from generators import mcp as gen
        elif ptype == "rest":
            try:
                from .generators import rest as gen
            except Exception:
                from generators import rest as gen
        elif ptype == "cli":
            try:
                from .generators import cli as gen
            except Exception:
                from generators import cli as gen
        elif ptype == "python_pkg":
            try:
                from .generators import python_pkg as gen
            except Exception:
                from generators import python_pkg as gen
        elif ptype == "skill_bundle":
            try:
                from .generators import skill_bundle as gen
            except Exception:
                from generators import skill_bundle as gen
        else:
            # unknown: create skeleton native plugin
            out = Path(output_dir) / name
            out.mkdir(parents=True, exist_ok=True)
            (out / "plugin.yaml").write_text(
                f"name: {name}\nversion: 1.0.0\ndescription: \"Skeleton generated (unknown type)\"\nauthor: BlueStarJL\nlicense: MIT\nrequires_hermes: \">=0.22.0\"\nprovides_tools: []\nprovides_hooks: []\nrequires_env: []\n",
                encoding="utf-8",
            )
            (out / "DETECTION_REPORT.md").write_text(json.dumps(rep, indent=2), encoding="utf-8")
            (out / "README.md").write_text(f"# {name}\n\nManual completion required.\n", encoding="utf-8")
            gen_result = {"output_path": str(out), "files": []}
            # skip generator
            gen = None
            gen_result = {"output_path": str(out), "files": sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file())}
        if ptype != "unknown":
            gen_result = gen.generate(repo_path, output_dir, name, rep)
    except Exception as e:
        return _json({"success": False, "error": f"generation failed: {e}", "report": rep})

    out_path = gen_result["output_path"]

    # validate
    validation = "fail"
    report_val = ""
    try:
        r = subprocess.run(["hermes", "plugins", "validate", out_path, "--install-deps"], capture_output=True, text=True, timeout=300)
        report_val = (r.stdout or "") + (r.stderr or "")
        validation = "pass" if r.returncode == 0 else "fail"
    except Exception as e:
        report_val = str(e)

    # doctor
    doctor_report = ""
    try:
        r2 = subprocess.run(["hermes", "plugins", "doctor", out_path, "--ci"], capture_output=True, text=True, timeout=300)
        doctor_report = (r2.stdout or "") + (r2.stderr or "")
    except Exception as e:
        doctor_report = str(e)

    return _json({
        "success": True,
        "output_path": out_path,
        "type": ptype,
        "validation": validation,
        "validation_report": report_val,
        "doctor_report": doctor_report,
        "files": gen_result.get("files", []),
    })


def handle_validate_plugin(args: Dict[str, Any], **_kw) -> str:
    path = (args.get("path") or "").strip()
    if not path:
        return _json({"success": False, "error": "path required"})
    val_report = ""
    doc_report = ""
    val_code = 1
    doc_code = 1
    try:
        r = subprocess.run(["hermes", "plugins", "validate", path, "--install-deps"], capture_output=True, text=True, timeout=300)
        val_report = (r.stdout or "") + (r.stderr or "")
        val_code = r.returncode
    except Exception as e:
        val_report = str(e)
    try:
        r2 = subprocess.run(["hermes", "plugins", "doctor", path, "--ci"], capture_output=True, text=True, timeout=300)
        doc_report = (r2.stdout or "") + (r2.stderr or "")
        doc_code = r2.returncode
    except Exception as e:
        doc_report = str(e)
    return _json({
        "success": val_code == 0 and doc_code == 0,
        "validate_exit": val_code,
        "doctor_exit": doc_code,
        "validation_report": val_report,
        "doctor_report": doc_report,
    })
