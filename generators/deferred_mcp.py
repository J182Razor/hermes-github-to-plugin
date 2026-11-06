from __future__ import annotations
import ast
import hashlib
import json
import re
import stat
from pathlib import Path
import importlib.util

_spec = importlib.util.spec_from_file_location('_g2h_metadata_io', Path(__file__).parent.parent / 'metadata_io.py')
_metadata_io = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_metadata_io)
METADATA_LIMIT = 1024 * 1024


def generate(repo_path, out_dir, name, report):
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', name) or '..' in name:
        raise ValueError('invalid output name')
    root = Path(repo_path)
    config = root / 'mcp.json'
    console_function = None
    requirements = []
    identity = None
    if not config.exists() and not config.is_symlink():
        if not report.get('_explicit_mcp') and report.get('type') != 'mcp':
            raise ValueError('pyproject console MCP conversion requires detected or explicit type mcp')
        for directory in (root, root / 'src'):
            try:
                info = directory.lstat()
            except OSError as exc:
                raise ValueError('invalid source root') from exc
            if not stat.S_ISDIR(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError('source root must be a non-link directory')
        metadata = root / 'pyproject.toml'
        info = metadata.lstat()
        if not stat.S_ISREG(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('pyproject.toml must be a regular file')
        data = _metadata_io.toml_data(metadata, METADATA_LIMIT)
        project = data.get('project', {})
        if not isinstance(project, dict):
            raise ValueError('invalid project metadata')
        project_name, version = project.get('name'), project.get('version')
        if (not isinstance(project_name, str) or len(project_name) > 128
                or not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?', project_name, re.ASCII)
                or not isinstance(version, str) or len(version) > 64
                or not re.fullmatch(r'[0-9]+(?:\.[0-9]+)*', version, re.ASCII)
                or not isinstance(project.get('dynamic', []), list)
                or any(x in project.get('dynamic', []) for x in ('name', 'version'))):
            raise ValueError('static project name and numeric version required')
        identity = {'name': project_name, 'version': version}
        spec = importlib.util.spec_from_file_location('_g2h_fixed', Path(__file__).with_name('fixed_adapter.py'))
        fixed = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixed)
        requirements = fixed._requirements(project, 'cli')
        normalize = lambda s: re.sub(r'[-_.]+', '-', s).lower()
        if any(normalize(re.match(r'[A-Za-z0-9._-]+', req).group()) == normalize(project_name) for req in requirements):
            raise ValueError('project identity conflicts with dependency')
        if sum(bool(re.match(r'(?i)^mcp(?:[ \t]*(?:==|!=|<=|>=|<|>)|$)', r)) for r in requirements) != 1:
            raise ValueError('exactly one mcp dependency is required')
        scripts = project.get('scripts', {})
        if not isinstance(scripts, dict) or len(scripts) != 1:
            raise ValueError('exactly one project.scripts entry is required')
        script_name, entry = next(iter(scripts.items()))
        if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}', script_name, re.ASCII) or not isinstance(entry, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*:[A-Za-z_][A-Za-z0-9_]*', entry, re.ASCII):
            raise ValueError('invalid console entrypoint')
        package, console_function = entry.split(':')
        spec = importlib.util.spec_from_file_location('_g2h_package_mcp', Path(__file__).with_name('package_mcp.py'))
        package_helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(package_helper)
        package_files, package_hashes = package_helper.collect(root / 'src', package, _metadata_io.read_bounded, console_function=console_function, requirements=requirements, identity=identity)
        if not report.get('_explicit_mcp'):
            package_helper.require_fastmcp_evidence(package_files, package, console_function)
        args = ['-m', package]
        package_mode = True
        digest = None
    else:
        return _generate_manifest(root, out_dir, name, report)
    return _write_output(out_dir, name, args, package_mode, digest, package_files, package_hashes, None, console_function, requirements, identity)


def _generate_manifest(root, out_dir, name, report):
    config = root / 'mcp.json'
    if config.is_symlink() or not config.is_file():
        raise ValueError('a non-symlink mcp.json is required')
    raw = _metadata_io.read_bounded(config, METADATA_LIMIT)
    try:
        data = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('invalid UTF-8 JSON metadata') from exc
    servers = data.get('mcpServers') if isinstance(data, dict) else None
    if not isinstance(servers, dict) or len(servers) != 1:
        raise ValueError('exactly one MCP server is required')
    server, cfg = next(iter(servers.items()))
    if not re.fullmatch(r'[A-Za-z0-9_-]+', server) or not isinstance(cfg, dict) or set(cfg) - {'type', 'command', 'args'}:
        raise ValueError('unsupported MCP server configuration; environment overrides are forbidden')
    if cfg.get('type', 'stdio') != 'stdio' or cfg.get('command') not in ('python', 'python3'):
        raise ValueError('only local Python stdio scripts are supported')
    args = cfg.get('args')
    package_mode = isinstance(args, list) and len(args) == 2 and args[0] == '-m' and isinstance(args[1], str)
    if package_mode:
        spec = importlib.util.spec_from_file_location('_g2h_package_mcp', Path(__file__).with_name('package_mcp.py'))
        package_helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(package_helper)
        package_files, package_hashes = package_helper.collect(root, args[1], _metadata_io.read_bounded)
        digest = None
    else:
        if not isinstance(args, list) or len(args) != 1 or not isinstance(args[0], str) or not re.fullmatch(r'[A-Za-z0-9_-]+\.py', args[0]):
            raise ValueError('one top-level Python script argument required')
        source = root / args[0]
        if source.is_symlink() or not source.is_file() or source.resolve().parent != root.resolve():
            raise ValueError('server script must be a non-symlink top-level file')
        contents = _metadata_io.read_bounded(source, 12000)
        if len(contents) > 12000:
            raise ValueError('server script exceeds 12000 byte Windows command-line bound')
        try:
            tree = ast.parse(contents, filename=args[0])
        except (SyntaxError, ValueError) as exc:
            raise ValueError('invalid Python server script') from exc
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level:
                raise ValueError('relative imports are unsupported')
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                modules = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or '']
                for module in modules:
                    top = module.split('.')[0]
                    if (root / (top + '.py')).exists() or (root / top).is_dir():
                        raise ValueError('sibling imports are unsupported: ' + top)
            if isinstance(node, ast.Name) and node.id in {'__file__', '__import__', 'eval', 'exec'}:
                raise ValueError('dynamic imports or __file__ are unsupported')
            if isinstance(node, ast.Attribute) and node.attr in {'import_module', 'find_spec', 'spec_from_file_location', 'SourceFileLoader'}:
                raise ValueError('dynamic imports are unsupported')
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {'open', 'Path'} and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str) and not Path(node.args[0].value).is_absolute():
                raise ValueError('relative resources are unsupported')
        digest = hashlib.sha256(contents).hexdigest()
    return _write_output(out_dir, name, args, package_mode, digest, package_files if package_mode else {}, package_hashes if package_mode else {}, contents if not package_mode else None, None, [])


def _write_output(out_dir, name, args, package_mode, digest, package_files, package_hashes, contents, console_function, requirements, identity=None):
    prefix = 'g2h_' + hashlib.sha256(name.encode()).hexdigest()[:20]
    manifest_name = name if len(name) <= 64 and re.fullmatch(r'(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', name) else 'g2h-' + hashlib.sha256(name.encode()).hexdigest()[:24]
    out = Path(out_dir) / name
    out.mkdir(parents=True, exist_ok=False)
    if package_mode:
        for relative, contents in package_files.items():
            destination = out / 'source' / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(contents)
    else:
        (out / 'source.py').write_bytes(contents)
    (out / 'plugin.json').write_text(json.dumps({'$schema':'https://agent-plugins.org/schemas/1.0.0/plugin.schema.json','name':manifest_name,'version':'1.0.0','description':'Deferred reviewed MCP server','extensions':{'com.nousresearch.hermes':{'servers':{prefix:{'trust':'untrusted'}}}}}, indent=2), encoding='utf-8')
    (out / 'mcp.json').write_text(json.dumps({'$schema':'https://agent-plugins.org/schemas/1.0.0/mcp.schema.json','mcpServers':{prefix:{'type':'stdio','command':'python','args':['${PLUGIN_ROOT}/server.py','${PLUGIN_DATA}/persistent-mcp-opt-in.txt']}}}, indent=2), encoding='utf-8')
    templates = Path(__file__).parent
    (out / 'adapter.py').write_text('DIGEST = ' + repr(digest) + '\nPACKAGE_MODULE = ' + repr(args[1] if package_mode else None) + '\nPACKAGE_HASHES = ' + repr(package_hashes if package_mode else {}) + '\nCONSOLE_FUNCTION = ' + repr(console_function) + '\nCONSOLE_REQUIREMENTS = ' + repr(requirements) + '\nCONSOLE_IDENTITY = ' + repr(identity) + '\n' + (templates / 'contained_runtime.py.txt').read_text(encoding='utf-8') + '\n' + (templates / 'deferred_adapter.py.txt').read_text(encoding='utf-8'), encoding='utf-8')
    for filename, template in [('server.py','deferred_server.py.txt'), ('worker.py','deferred_worker.py.txt')]:
        content = (templates / template).read_text(encoding='utf-8')
        if filename == 'worker.py':
            content = (templates / 'package_envelope.py').read_text(encoding='utf-8') + '\n' + content
        (out / filename).write_text(content, encoding='utf-8')
    (out / 'README.md').write_text('Operator opt-in: after reviewing and trusting the source, create ${PLUGIN_DATA}/persistent-mcp-opt-in.txt with exactly these UTF-8 bytes and no newline: I TRUST THIS SOURCE WITH PERSISTENT FULL OS AUTHORITY. This is an operator control, not protection against an agent or process with the same OS account and filesystem access. Host approval remains governed by host policy for each invocation; do not treat simulated callbacks as human approval. Removing the file blocks the next invocation, not already running background code. Source and descendants retain full OS authority and may run between calls and after later denials, until shutdown, failure, an active-call timeout, or restart; idle time has no automatic timeout. Trust the code before enabling it. Windows job limits cap committed memory at 256 MiB per process and 512 MiB aggregate; these are resource limits, not a security sandbox, and do not cap the parent host process. The 1 MiB serialized response limit is checked only after the MCP SDK parses the source response; it is not a streaming or pre-parse memory bound. Process termination and reaping may exceed the request deadline.\n\n' + 'Review all files before use or sharing; source may contain secrets. The source process and session persist between approved calls until shutdown, failure or timeout. Static checks cannot detect every dynamic import or resource dependency. Package sources are copied without a comprehensive secret scan. Source bytes pass through the child stdin pipe, not its command line; privileged local processes and approved source may still access them. Never embed secrets.\n\nThe installed Hermes portable loader accepts only bare executable names or plugin-local executable paths for command, and does not interpolate environment variables there. This artifact requests python: the operator must explicitly put the intended Python interpreter (with the MCP SDK and reviewed source dependencies installed) on the Hermes process PATH as python, or edit the generated command to an approved bare executable. Verify using that same environment; no dependency is installed automatically. A missing interpreter prevents discovery; a missing SDK prevents server startup.\n\nOnly Windows is supported; invocation fails closed if a job cannot be assigned. Hermes untrusted trust requests per-call approval under host policy, which users may override. Approved source has full OS rights and may have side effects; neither Python isolation nor job containment is a security sandbox. Digest detects changes to source only, not malicious edits to adapter files.\n', encoding='utf-8')
    if console_function:
        with (out / 'README.md').open('a', encoding='utf-8') as doc:
            doc.write('\nConsole entrypoints must be one undecorated synchronous top-level function, directly defined or re-exported once from a sibling module (aliases are supported). Obvious import-time cycles and ambiguous bindings are rejected; deferred imports inside functions are not import-time cycles. Some dynamic mutations, including globals().update with unrelated keys, are conservatively unsupported. After approved source import, the function binding is checked against its reviewed source code before invocation. This compatibility check is not a security boundary: approved source has already executed and can evade checks.\n')
            doc.write('\nOnly the reviewed static project Name and numeric Version are exposed in memory via importlib.metadata, after dependency checks. Installed distributions with the same normalized name are rejected. Distribution files, resources, entry points and other metadata are unsupported; this is not an installed distribution.\n')
            doc.write('\nConsole startup arguments: optionally create ${PLUGIN_DATA}/mcp-startup-args.json as a UTF-8 JSON array of up to 64 strings, each at most 4096 characters and without NUL or Unicode surrogate codepoints; maximum file size is 16 KiB. A missing file means []. It must be a regular non-link file. It is read only on approved invocations, passed via the stdin bootstrap as separate argv tokens, never through a shell or OS command line. This is not complete confidentiality: source can emit arguments in output reaching host logs or conversation, and code with the same OS authority may access them. Never put secrets in arguments. The bytes are bound for the worker lifetime: changing them to a valid configuration causes the next approved call to fail and close the worker; retry starts a new worker. Invalid Unicode is rejected without changing a live worker.\n')
    return {'output_path':str(out), 'files':sorted(p.name for p in out.iterdir()), 'status':'partial: review and runtime dependencies required'}
