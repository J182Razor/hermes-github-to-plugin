from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List


def _run(cmd, cwd=None, timeout=120):
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except Exception as e:
        return 1, "", str(e)


def clone_repo(repo_url: str, dest: str = None) -> str:
    if dest is None:
        dest = tempfile.mkdtemp(prefix="g2h_")
    else:
        os.makedirs(dest, exist_ok=True)
    if os.path.exists(os.path.join(dest, ".git")):
        return dest
    # clean
    if os.path.exists(dest):
        import shutil
        shutil.rmtree(dest, ignore_errors=True)
        os.makedirs(dest, exist_ok=True)
    code, out, err = _run(["git", "clone", "--depth", "1", repo_url, dest], timeout=300)
    if code != 0:
        raise RuntimeError(f"git clone failed: {err or out}")
    return dest


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_yaml(path: Path):
    try:
        import yaml
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def detect_type(repo_path: str) -> Dict[str, Any]:
    root = Path(repo_path)
    evidence: List[str] = []
    # collect all files
    files = []
    for p in root.rglob("*"):
        if p.is_file():
            rel = str(p.relative_to(root)).replace("\\", "/")
            if ".git/" in rel:
                continue
            files.append(rel)
    fileset = set(files)

    # MCP
    mcp_score = 0
    mcp_evidence = []
    if "mcp.json" in fileset:
        mcp_score += 3
        mcp_evidence.append("mcp.json present")
    # package.json scripts mcp
    pkg = _load_json(root / "package.json")
    if isinstance(pkg, dict):
        scripts = pkg.get("scripts") or {}
        for k, v in (scripts.items() if isinstance(scripts, dict) else []):
            if isinstance(v, str) and "mcp" in (k + " " + v).lower():
                mcp_score += 2
                mcp_evidence.append(f"package.json script {k}: {v}")
    # README mentions MCP server
    readme = ""
    for name in ("README.md", "README.rst", "README.txt", "README"):
        if (root / name).is_file():
            readme = _read_text(root / name)
            break
    if "MCP server" in readme or "mcp server" in readme.lower():
        mcp_score += 1
        mcp_evidence.append("README mentions MCP server")
    # entrypoints
    mcp_entrypoints = []
    for cand in ("server/index.ts", "server/index.js", "src/mcp.py", "mcp_server.py", "server.py"):
        if cand in fileset:
            mcp_entrypoints.append(cand)
            mcp_score += 1
            mcp_evidence.append(f"entrypoint {cand}")
    # pyproject console_scripts mcp
    pyproject = _load_yaml(root / "pyproject.toml") if False else None
    # parse pyproject manually-ish via tomllib if available
    try:
        import tomllib
        if (root / "pyproject.toml").is_file():
            with open(root / "pyproject.toml", "rb") as f:
                pp = tomllib.load(f)
            scripts = ((pp.get("project") or {}).get("scripts") or {})
            for k, v in scripts.items():
                if "mcp" in (k + " " + str(v)).lower():
                    mcp_score += 2
                    mcp_evidence.append(f"pyproject console_script {k}: {v}")
                    mcp_entrypoints.append(str(v))
    except Exception:
        pass

    # OpenAPI/REST
    rest_score = 0
    rest_evidence = []
    for cand in ("openapi.yaml", "openapi.json", "swagger.yaml", "swagger.json", "openapi.yml"):
        if cand in fileset:
            rest_score += 3
            rest_evidence.append(f"{cand} present")
    if (root / "routes").is_dir() or (root / "api").is_dir():
        rest_score += 2
        rest_evidence.append("routes/ or api/ directory present")
    # README base url + endpoints heuristic
    if re.search(r"https?://[^\s]+", readme) and ("endpoint" in readme.lower() or "GET " in readme or "POST " in readme):
        rest_score += 1
        rest_evidence.append("README mentions endpoints/HTTP methods")

    # CLI
    cli_score = 0
    cli_evidence = []
    try:
        import tomllib
        if (root / "pyproject.toml").is_file():
            with open(root / "pyproject.toml", "rb") as f:
                pp = tomllib.load(f)
            scripts = ((pp.get("project") or {}).get("scripts") or {})
            if scripts:
                cli_score += 2
                cli_evidence.append(f"pyproject [project.scripts]: {list(scripts.keys())}")
    except Exception:
        pass
    # setup.py entry_points
    setup_py = _read_text(root / "setup.py")
    if "entry_points" in setup_py:
        cli_score += 2
        cli_evidence.append("setup.py entry_points present")
    if (root / "bin").is_dir():
        cli_score += 2
        cli_evidence.append("bin/ directory present")
    if re.search(r"Usage:\s*\S+", readme):
        cli_score += 1
        cli_evidence.append("README contains Usage:")

    # Python package
    py_score = 0
    py_evidence = []
    if (root / "pyproject.toml").is_file():
        try:
            import tomllib
            with open(root / "pyproject.toml", "rb") as f:
                pp = tomllib.load(f)
            if pp.get("project"):
                py_score += 2
                py_evidence.append("pyproject [project] present")
        except Exception:
            pass
    # importable module: any top-level .py or package dir
    top_py = [f for f in files if "/" not in f and f.endswith(".py")]
    pkg_dirs = [d for d in os.listdir(root) if os.path.isdir(root / d) and (root / d / "__init__.py").is_file()]
    if top_py or pkg_dirs:
        py_score += 1
        py_evidence.append(f"python modules: {top_py[:5]} {pkg_dirs[:5]}")

    # Skill collection
    skill_score = 0
    skill_evidence = []
    skill_dirs = []
    skills_root = root / "skills"
    if skills_root.is_dir():
        for child in skills_root.iterdir():
            if child.is_dir() and (child / "SKILL.md").is_file():
                skill_dirs.append(child.name)
        if skill_dirs:
            skill_score += 3
            skill_evidence.append(f"skills/*/SKILL.md: {skill_dirs}")
    # .claude-plugin
    if (root / ".claude-plugin").is_dir() or (root / ".claude").is_dir():
        skill_score += 1
        skill_evidence.append("Claude plugin metadata present")

    # Determine type by priority
    candidates = [
        ("mcp", mcp_score, mcp_evidence),
        ("rest", rest_score, rest_evidence),
        ("cli", cli_score, cli_evidence),
        ("python_pkg", py_score, py_evidence),
        ("skill_bundle", skill_score, skill_evidence),
    ]
    # unknown if all zero
    best = max(candidates, key=lambda c: c[1])
    if best[1] <= 0:
        detected = "unknown"
        best_evidence = ["no strong signals detected"]
    else:
        detected = best[0]
        best_evidence = best[2]

    # suggested name from repo dir or package
    suggested_name = root.name
    # env vars heuristic
    env_vars = []
    for text in (readme,):
        for m in re.finditer(r"\b([A-Z][A-Z0-9_]{2,})\b", text):
            v = m.group(1)
            if v not in env_vars and ("KEY" in v or "TOKEN" in v or "URL" in v or "SECRET" in v):
                env_vars.append(v)
    # endpoints heuristic for rest
    endpoints = []
    if detected == "rest":
        for m in re.finditer(r"(GET|POST|PUT|DELETE|PATCH)\s+(/\S+)", readme):
            endpoints.append(m.group(0))

    # entrypoints
    entrypoints = mcp_entrypoints if detected == "mcp" else []
    if detected == "cli":
        # from pyproject scripts
        try:
            import tomllib
            if (root / "pyproject.toml").is_file():
                with open(root / "pyproject.toml", "rb") as f:
                    pp = tomllib.load(f)
                entrypoints = list(((pp.get("project") or {}).get("scripts") or {}).values())
        except Exception:
            pass

    report = {
        "type": detected,
        "evidence": best_evidence,
        "all_scores": {c[0]: c[1] for c in candidates},
        "all_evidence": {c[0]: c[2] for c in candidates},
        "suggested_name": suggested_name,
        "entrypoints": entrypoints,
        "env_vars": env_vars,
        "endpoints": endpoints,
        "repo_path": str(root),
    }
    return report


def detect_repo(repo_url: str) -> Dict[str, Any]:
    path = clone_repo(repo_url)
    rep = detect_type(path)
    rep["repo_url"] = repo_url
    return rep
