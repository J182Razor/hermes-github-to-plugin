from __future__ import annotations

import json
import hashlib
import os
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List
import importlib.util

_spec = importlib.util.spec_from_file_location('_g2h_metadata_io', Path(__file__).parent / 'metadata_io.py')
_metadata_io = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_metadata_io)
METADATA_LIMIT = 1024 * 1024


def _run(cmd, cwd=None, timeout=120, env=None):
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env, stdin=subprocess.DEVNULL)
        return r.returncode, r.stdout, r.stderr
    except Exception as e:
        return 1, "", str(e)


_clone_identities = {}


class InvalidRepoURL(ValueError):
    pass


class CloneAccessError(RuntimeError):
    pass


class WindowsACLSupportError(RuntimeError):
    pass


def default_plugin_name(repo_url):
    if not isinstance(repo_url, str) or not re.fullmatch(
        r"https://github\.com/[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]+", repo_url
    ):
        raise InvalidRepoURL('expected a credential-free GitHub HTTPS owner/repo URL')
    owner, repo = repo_url.removeprefix('https://github.com/').split('/')
    if repo.lower().endswith('.git'):
        repo = repo[:-4]
    if not repo or repo in ('.', '..'):
        raise InvalidRepoURL('expected a credential-free GitHub HTTPS owner/repo URL')
    canonical = (owner + '/' + repo).lower()
    slug = re.sub(r'[^a-z0-9]+', '-', owner.lower() + '-' + repo.lower()).strip('-')
    # The digest distinguishes normalization and truncation collisions; prefix avoids
    # Windows device names, and the entire component is bounded to 64 characters.
    digest = hashlib.sha256(canonical.encode('ascii')).hexdigest()[:16]
    return 'g2h-' + slug[:43].rstrip('-') + '-' + digest


def reject_link_components(path):
    """Reject existing symlinks and Windows reparse points without resolving them."""
    path = Path(os.path.abspath(path))
    for component in reversed((path, *path.parents)):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('symlink or reparse point in filesystem path')
    return path


def _cleanup_clone(path):
    expected = _clone_identities.pop(path, None)
    if expected is None:
        raise RuntimeError('refusing cleanup of unverified clone')
    reject_link_components(path)
    info = os.lstat(path)
    if (info.st_dev, info.st_ino) != expected:
        raise RuntimeError('refusing cleanup of replaced clone')
    def remove_readonly(func, target, exc):
        if os.path.islink(target) or not isinstance(exc[1], PermissionError):
            raise exc[1]
        os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
        func(target)
    shutil.rmtree(path, onerror=remove_readonly)


def _windows_private_clone_dir(parent):
    # pywintypes must be loaded first so its DLL is available to the extensions.
    try:
        import pywintypes
        import win32security
        import ntsecuritycon
        import win32file
        import win32api
    except ImportError as exc:
        raise WindowsACLSupportError('Windows private ACL support unavailable') from exc
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
    sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    flags = ntsecuritycon.OBJECT_INHERIT_ACE | ntsecuritycon.CONTAINER_INHERIT_ACE
    acl = win32security.ACL()
    acl.AddAccessAllowedAceEx(win32security.ACL_REVISION, flags, ntsecuritycon.FILE_ALL_ACCESS, sid)
    sd = win32security.SECURITY_DESCRIPTOR()
    sd.SetSecurityDescriptorOwner(sid, False)
    sd.SetSecurityDescriptorDacl(True, acl, False)
    sd.SetSecurityDescriptorControl(win32security.SE_DACL_PROTECTED, win32security.SE_DACL_PROTECTED)
    attrs = pywintypes.SECURITY_ATTRIBUTES()
    attrs.SECURITY_DESCRIPTOR = sd
    for _ in range(32):
        path = str(parent / ('g2h_' + secrets.token_hex(16)))
        try:
            win32file.CreateDirectory(path, attrs)
        except pywintypes.error as exc:
            if exc.winerror == 183:
                continue
            raise
        info = os.lstat(path)
        _clone_identities[path] = (info.st_dev, info.st_ino)
        try:
            reject_link_components(path)
            found = win32security.GetNamedSecurityInfo(path, win32security.SE_FILE_OBJECT,
                win32security.OWNER_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION)
            dacl = found.GetSecurityDescriptorDacl()
            if (found.GetSecurityDescriptorOwner() != sid or dacl is None
                    or not found.GetSecurityDescriptorControl()[0] & win32security.SE_DACL_PROTECTED
                    or dacl.GetAceCount() != 1
                    or dacl.GetAce(0) != ((win32security.ACCESS_ALLOWED_ACE_TYPE, flags), ntsecuritycon.FILE_ALL_ACCESS, sid)):
                raise RuntimeError('Windows clone directory ACL verification failed')
        except BaseException:
            _cleanup_clone(path)
            raise
        return path
    raise RuntimeError('could not allocate private clone directory')


def clone_repo(repo_url: str, dest: str = None) -> str:
    # Do not accept options, credentials, local paths, redirects, or non-GitHub hosts.
    default_plugin_name(repo_url)
    if dest is not None:
        raise ValueError("explicit clone destinations are not supported")
    parent = reject_link_components(tempfile.gettempdir())
    if os.name == 'nt':
        dest = _windows_private_clone_dir(parent)
    else:
        dest = tempfile.mkdtemp(prefix="g2h_", dir=str(parent))
        info = os.lstat(dest)
        _clone_identities[dest] = (info.st_dev, info.st_ino)
        if stat.S_IMODE(info.st_mode) & 0o077:
            _cleanup_clone(dest)
            raise RuntimeError('clone directory is not private')
    env = {k: os.environ[k] for k in ('PATH', 'SYSTEMROOT', 'SystemRoot', 'WINDIR', 'COMSPEC', 'PATHEXT', 'TEMP', 'TMP', 'TMPDIR') if k in os.environ}
    env.update({'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
                'GIT_TERMINAL_PROMPT': '0', 'GCM_INTERACTIVE': 'Never', 'GIT_ATTR_NOSYSTEM': '1'})
    try:
        code, out, err = _run(["git", "-c", "http.followRedirects=false", "-c", "credential.helper=", "-c", "core.hooksPath=" + os.devnull, "-c", "init.templateDir=", "clone", "--depth", "1", "--", repo_url, dest], timeout=300, env=env)
        if code != 0:
            raise CloneAccessError("git clone failed; check repository access and network")
    except BaseException:
        _cleanup_clone(dest)
        raise
    return dest


def _read_text(path: Path) -> str:
    if not path.exists() and not path.is_symlink():
        return ""
    return _metadata_io.read_bounded(path, METADATA_LIMIT).decode('utf-8', errors='ignore')


def _load_json(path: Path):
    if not path.exists() and not path.is_symlink():
        return None
    raw = _metadata_io.read_bounded(path, METADATA_LIMIT)
    try:
        return json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _load_yaml(path: Path):
    if not path.exists() and not path.is_symlink():
        return None
    raw = _metadata_io.read_bounded(path, METADATA_LIMIT)
    try:
        import yaml
        return yaml.safe_load(raw.decode('utf-8'))
    except Exception:
        return None


def detect_type(repo_path: str) -> Dict[str, Any]:
    root = Path(repo_path)
    metadata = root / 'pyproject.toml'
    pp = {}
    if metadata.exists() or metadata.is_symlink():
        raw = _metadata_io.read_bounded(metadata, METADATA_LIMIT)
        try:
            import tomllib
            pp = tomllib.loads(raw.decode('utf-8'))
        except (UnicodeDecodeError, tomllib.TOMLDecodeError):
            pass
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
                mcp_evidence.append("package.json MCP script present")
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
            scripts = ((pp.get("project") or {}).get("scripts") or {})
            for k, v in scripts.items():
                if "mcp" in (k + " " + str(v)).lower():
                    mcp_score += 2
                    mcp_evidence.append("pyproject MCP console script present")
    except Exception:
        pass

    # OpenAPI/REST
    rest_score = 0
    rest_evidence = []
    for cand in ("openapi.yaml", "openapi.json", "swagger.yaml", "swagger.json", "openapi.yml", "swagger.yml"):
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
            scripts = ((pp.get("project") or {}).get("scripts") or {})
            if scripts:
                cli_score += 2
                cli_evidence.append("pyproject [project.scripts] present")
            if isinstance(scripts, dict) and len(scripts) == 1:
                entry = next(iter(scripts.values()))
                project = pp.get('project', {})
                if (isinstance(entry, str) and re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*', entry, re.ASCII)
                        and any((base.joinpath(*entry.split(':')[0].split('.')).with_suffix('.py')).is_file() for base in (root, root / 'src'))
                        and not project.get('dynamic')):
                    cli_score += 2
                    cli_evidence.append('candidate Python CLI entrypoint; generator validation required')
                    if project.get('dependencies') or project.get('optional-dependencies') or project.get('dynamic'):
                        cli_evidence.append('declared dependencies require adapter validation and manual provisioning; optional and dynamic dependencies are unsupported')
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
        py_evidence.append(f"python modules: {len(top_py)} files, {len(pkg_dirs)} packages")

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
            skill_evidence.append(f"skills/*/SKILL.md count: {len(skill_dirs)}")
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
    elif sum(c[1] == best[1] for c in candidates) > 1:
        detected = "unknown"
        best_evidence = ["ambiguous detection: " + ", ".join(c[0] for c in candidates if c[1] == best[1])]
    else:
        detected = best[0]
        best_evidence = best[2]

    # Scoring is only a candidate signal: console MCP classification also needs
    # independently reviewed source syntax. Never import repository code here.
    project = pp.get('project', {}) if isinstance(pp, dict) else {}
    scripts = project.get('scripts', {}) if isinstance(project, dict) else {}
    if detected == 'mcp' and 'mcp.json' not in fileset and isinstance(scripts, dict) and scripts:
        try:
            if len(scripts) != 1:
                raise ValueError('multiple scripts')
            entry = next(iter(scripts.values()))
            if not isinstance(entry, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*:[A-Za-z_][A-Za-z0-9_]*', entry, re.ASCII):
                raise ValueError('unsupported entry')
            package, function = entry.split(':')
            reject_link_components(root / 'src' / package)
            spec = importlib.util.spec_from_file_location('_g2h_package_mcp_detect', Path(__file__).parent / 'generators' / 'package_mcp.py')
            helper = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(helper)
            source, _ = helper.collect(root / 'src', package, _metadata_io.read_bounded, console_function=function)
            helper.require_fastmcp_evidence(source, package, function)
        except (OSError, ValueError, SyntaxError, UnicodeError, KeyError):
            detected = 'unknown'
            best_evidence = ['ambiguous MCP console source; inspect it and specify type=mcp or type=cli']

    # suggested name from repo dir or package
    suggested_name = 'converted-plugin'
    # Uppercase README tokens are not reliably distinguishable from secrets.
    env_vars = []
    # README paths can include credentials; do not report them.
    endpoints = []

    # entrypoints
    entrypoints = mcp_entrypoints if detected == "mcp" else []
    if detected == "cli":
        # from pyproject scripts
        try:
            import tomllib
            if (root / "pyproject.toml").is_file():
                entrypoints = []  # Untrusted metadata may embed credentials.
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

    }
    return report


def detect_repo(repo_url: str, retain_clone: bool = False) -> Dict[str, Any]:
    suggested_name = default_plugin_name(repo_url)
    path = clone_repo(repo_url)
    try:
        rep = detect_type(path)
    except BaseException:
        _cleanup_clone(path)
        raise
    rep["repo_url"] = repo_url
    rep["suggested_name"] = suggested_name
    if retain_clone:
        rep['repo_path'] = path
        rep['_cleanup'] = lambda: _cleanup_clone(path)
    else:
        _cleanup_clone(path)
        rep.pop('repo_path', None)
    return rep
