from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import re
import stat
import tomllib
from pathlib import Path
import importlib.util

_spec = importlib.util.spec_from_file_location('_g2h_metadata_io', Path(__file__).parent.parent / 'metadata_io.py')
_metadata_io = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_metadata_io)
METADATA_LIMIT = 1024 * 1024


def _read_bounded(path, limit):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise ValueError('symlink or reparse point in source')
    if info.st_size > limit:
        raise ValueError('source file too large')
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as stream:
        actual = os.fstat(stream.fileno())
        if not stat.S_ISREG(actual.st_mode) or getattr(actual, 'st_file_attributes', 0) & 0x400 or (info.st_dev, info.st_ino) != (actual.st_dev, actual.st_ino):
            raise ValueError('symlink or reparse point in source')
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('source file too large')
    return raw


def generate(repo_path, out_dir, name, report, single_file=False):
    root = Path(repo_path)
    metadata = root / 'pyproject.toml'
    if metadata.is_symlink() or not metadata.is_file():
        raise ValueError('a non-symlink pyproject.toml is required')
    data = _metadata_io.toml_data(metadata, METADATA_LIMIT)
    project = data.get('project', {})
    if project.get('dynamic'):
        raise ValueError('dynamic project metadata is unsupported')
    dependencies = project.get('dependencies', [])
    if not isinstance(dependencies, list) or not all(isinstance(x, str) for x in dependencies):
        raise ValueError('invalid dependencies')
    optional = project.get('optional-dependencies', {})
    if not isinstance(optional, dict) or not all(isinstance(k, str) and isinstance(v, list) and all(isinstance(x, str) for x in v) for k, v in optional.items()):
        raise ValueError('invalid optional dependencies')
    if any(not re.fullmatch(r'[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_,.-]+\])?(?:\s*[<>=!~]+\s*[A-Za-z0-9.*+!_-]+(?:\s*,\s*[<>=!~]+\s*[A-Za-z0-9.*+!_-]+)*)?', dep) for dep in dependencies + [item for group in optional.values() for item in group]):
        raise ValueError('dependency specifiers with URLs or environment markers require manual review')
    kind = report['type']
    if kind == 'cli':
        scripts = project.get('scripts', {})
        if not isinstance(scripts, dict) or len(scripts) != 1:
            raise ValueError('exactly one project.scripts entry is required')
        entry = next(iter(scripts.values()))
    else:
        entry = data.get('tool', {}).get('github-to-hermes', {}).get('python-entrypoint')
    if not isinstance(entry, str) or not re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*', entry, re.ASCII):
        raise ValueError('invalid fixed module:function entrypoint')
    module, function = entry.split(':')
    parts = module.split('.')
    if len(parts) < 2 and not single_file:
        raise ValueError('package module required')
    if single_file:
        path = root / (module + '.py')
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('symlink or reparse point in source')
        raw = _read_bounded(path, 1048576)
        files = {module + '.py': raw}
    else:
        files = None
    candidates = [base for base in (root, root / 'src') if (base.joinpath(*parts).with_suffix('.py')).is_file()]
    if len(candidates) != 1:
        raise ValueError('entrypoint module missing or ambiguous')
    base = candidates[0]
    for path in (root, base):
        info = path.lstat()
        if path.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('symlink or reparse point in source path')
    package = base / parts[0]
    declarations = data.get('tool', {}).get('setuptools', {}).get('package-data', {})
    if not isinstance(declarations, dict):
        raise ValueError('invalid package-data')
    declared = set()
    for pkg_name, entries in declarations.items():
        if not isinstance(pkg_name, str) or not re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*', pkg_name, re.ASCII) or not pkg_name.split('.')[0] == parts[0] or not isinstance(entries, list):
            raise ValueError('unsupported package-data declaration')
        for entry in entries:
            if not isinstance(entry, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*\.(?:txt|json|csv|yaml|yml|toml|xml|html|md|dat)', entry, re.I) or re.search(r'(?:^|[_-])(secret|credential|password|token|private|key)(?:[_\-.]|$)', entry, re.I):
                raise ValueError('unsafe or unsupported package resource declaration')
            declared.add(pkg_name.replace('.', '/') + '/' + entry)
    if len(declared) > 128:
        raise ValueError('too many package resources')
    if files is None:
        files = {}
    total_bytes = sum(map(len, files.values()))
    resource_bytes = 0
    for directory, dirs, names in (() if single_file else os.walk(package, followlinks=False)):
        parent = Path(directory)
        info = parent.lstat()
        if parent.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('symlink or reparse point in source')
        for d in dirs:
            p = parent / d
            info = p.lstat()
            if p.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError('symlink or reparse point in source')
            if d.startswith('.'):
                raise ValueError('hidden package directory requires manual review')
            if d != '__pycache__' and not (p / '__init__.py').is_file():
                raise ValueError('non-package directory requires manual review')
        dirs[:] = [d for d in dirs if d != '__pycache__']
        for filename in names:
            rel = (parent / filename).relative_to(base).as_posix()
            resource = not filename.endswith('.py') or filename.startswith('.')
            if resource and rel not in declared:
                raise ValueError('undeclared package resource requires manual review')
            if re.search(r'(?:^|[_-])(secret|credential|password|token|private|key)(?:[_\-.]|$)', filename, re.I):
                raise ValueError('sensitive-looking source filename requires manual review')
            if len(files) >= 512: raise ValueError('too many package files')
            path = parent / filename
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError('symlink or reparse point in source')
            raw = _read_bounded(path, min(262144 if resource else 1048576, 8388608 - total_bytes, 1048576 - resource_bytes if resource else 8388608))
            total_bytes += len(raw)
            if resource: resource_bytes += len(raw)
            if resource:
                if len(raw) > 262144 or re.search(br'-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----', raw, re.I):
                    raise ValueError('oversized resource or private key canary')
            else:
                parsed = ast.parse(raw, filename=str(path))
                if declared and any(isinstance(n, ast.Name) and n.id == '__file__' for n in ast.walk(parsed)):
                    raise ValueError('__file__ resource access is unsupported')
            files[rel] = raw
    if declared - files.keys():
        raise ValueError('declared package resource missing')
    for item in declared:
        if item.rsplit('/', 1)[0] + '/__init__.py' not in files:
            raise ValueError('resource must belong to a regular package')
    if sum(len(files[k]) for k in declared) > 1048576:
        raise ValueError('package resources too large')
    for index in range(1, len(parts)):
        if '/'.join(parts[:index]) + '/__init__.py' not in files:
            raise ValueError('regular package __init__.py required')
    target = '/'.join(parts) + '.py'
    if target not in files:
        raise ValueError('entrypoint source excluded')
    tree = ast.parse(files[target])
    if single_file:
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ('exec', 'eval', 'compile', '__import__', 'globals', 'locals'):
                raise ValueError('dynamic execution or binding is unsupported')
            if isinstance(node, (ast.Import, ast.ImportFrom)) and any(a.name == 'importlib' or a.name.startswith('importlib.') for a in node.names):
                raise ValueError('dynamic imports are unsupported')
            if isinstance(node, ast.ImportFrom) and node.module and (node.module == 'importlib' or node.module.startswith('importlib.')):
                raise ValueError('dynamic imports are unsupported')
            if isinstance(node, ast.ImportFrom) and node.level:
                raise ValueError('relative imports require package conversion')
            imported = ([a.name for a in node.names] if isinstance(node, ast.Import) else [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
            for imported_name in imported:
                top = imported_name.split('.')[0]
                if top != module and ((root / (top + '.py')).exists() or (root / top).is_dir()):
                    raise ValueError('local imports require package conversion')
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name != function:
                raise ValueError('additional top-level definitions require manual review')
    definitions = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function]
    if len(definitions) != 1 or isinstance(definitions[0], ast.AsyncFunctionDef):
        raise ValueError('one synchronous top-level function required')
    fn = definitions[0]
    # Do not infer a signature when other top-level code can rebind it.
    # Imports and function/class definitions can themselves run arbitrary code;
    # source review and explicit approval remain essential.
    for node in tree.body:
        if node is fn:
            continue
        if isinstance(node, ast.Expr) and node.lineno < fn.lineno:
            # Pre-definition expressions may have side effects; they are not
            # executed during conversion or discovery, only after approval.
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            bound = [alias.asname or (alias.name.split('.')[0] if isinstance(node, ast.Import) else alias.name) for alias in node.names]
            if '*' in bound or any(x in bound for x in (function, 'str', 'int', 'float', 'bool')):
                raise ValueError('ambiguous entrypoint binding')
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name not in (function, 'str', 'int', 'float', 'bool') and not node.decorator_list:
            continue
        raise ValueError('ambiguous module-level execution or binding')
    if fn.returns is not None and not (isinstance(fn.returns, ast.Name) and fn.returns.id in ('str', 'int', 'float', 'bool')):
        raise ValueError('unsupported return annotation')
    if fn.decorator_list or fn.args.vararg or fn.args.kwarg:
        raise ValueError('dynamic or unsupported signature')
    schema = {'type': 'object', 'properties': {}, 'additionalProperties': False}
    if kind == 'cli':
        if fn.args.posonlyargs or fn.args.args or fn.args.kwonlyargs:
            raise ValueError('CLI entrypoint must take no Python arguments')
        schema['properties']['argv'] = {'type': 'array', 'items': {'type':'string'}}
    else:
        positional = fn.args.posonlyargs + fn.args.args
        args = positional + fn.args.kwonlyargs
        defaults = [None] * (len(positional)-len(fn.args.defaults)) + list(fn.args.defaults) + list(fn.args.kw_defaults)
        required = []
        for arg, default in zip(args, defaults):
            if not isinstance(arg.annotation, ast.Name) or arg.annotation.id not in ('str', 'int', 'float', 'bool'):
                raise ValueError('primitive annotations required')
            typ = {'str':'string','int':'integer','float':'number','bool':'boolean'}[arg.annotation.id]
            schema['properties'][arg.arg] = {'type':typ}
            if default is None:
                required.append(arg.arg)
            else:
                try:
                    val = ast.literal_eval(default)
                except (ValueError, TypeError):
                    raise ValueError('literal defaults required')
                expected = {'str':str,'int':int,'float':float,'bool':bool}[arg.annotation.id]
                if type(val) is not expected and not (expected is float and type(val) is int):
                    raise ValueError('default type mismatch')
                if isinstance(val, float) and not math.isfinite(val):
                    raise ValueError('non-finite default')
                schema['properties'][arg.arg]['default'] = val
        if required:
            schema['required'] = required
    json.dumps(schema, allow_nan=False)
    prefix = 'g2h_' + re.sub(r'[^a-z0-9_]', '_', name.lower())[:32] + '_' + hashlib.sha256(name.encode()).hexdigest()[:12]
    tool = prefix + '_run'
    out = Path(out_dir) / name
    out.mkdir(parents=True, exist_ok=False)
    for rel, raw in files.items():
        path = out / 'source' / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    digest = {k:hashlib.sha256(v).hexdigest() for k,v in files.items()}
    manifest_name = name if len(name) <= 64 and re.fullmatch(r'(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', name) else 'g2h-' + hashlib.sha256(name.encode()).hexdigest()[:24]
    plugin = {'$schema':'https://agent-plugins.org/schemas/1.0.0/plugin.schema.json','name':manifest_name,'version':'1.0.0','description':'Fixed reviewed entrypoint MCP adapter','extensions':{'com.nousresearch.hermes':{'servers':{prefix:{'trust':'untrusted'}}}}}
    config = {'$schema':'https://agent-plugins.org/schemas/1.0.0/mcp.schema.json','mcpServers':{prefix:{'type':'stdio','command':'python','args':['${PLUGIN_ROOT}/server.py']}}}
    (out/'plugin.json').write_text(json.dumps(plugin,indent=2),encoding='utf-8')
    (out/'mcp.json').write_text(json.dumps(config,indent=2),encoding='utf-8')
    header = ''.join(key + ' = ' + repr(value) + '\n' for key, value in [('MODULE',module),('FUNCTION',function),('KIND',kind),('TOOL',tool),('SCHEMA',schema),('DIGESTS',digest),('POSONLY',[a.arg for a in fn.args.posonlyargs])])
    (out/'adapter.py').write_text(header + Path(__file__).with_name('contained_runtime.py.txt').read_text(encoding='utf-8') + '\n' + Path(__file__).with_name('package_runtime.py.txt').read_text(encoding='utf-8'),encoding='utf-8')
    server = '''import asyncio, json, threading
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types
import adapter
async def list_tools(ctx, params):
 return types.ListToolsResult(tools=[types.Tool(name=adapter.TOOL, description="Run reviewed source with full OS privileges", inputSchema=adapter.SCHEMA, annotations=types.ToolAnnotations(readOnlyHint=False))])
async def call_tool(ctx, params):
 if params.name != adapter.TOOL: return types.CallToolResult(content=[types.TextContent(type="text", text="unknown tool")], isError=True)
 cancel=threading.Event()
 task=asyncio.create_task(asyncio.to_thread(adapter.handle, params.arguments or {}, cancel=cancel))
 try: result=json.loads(await asyncio.shield(task))
 except asyncio.CancelledError:
  cancel.set()
  raise
 return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(result))], isError=not result['success'])
async def main():
 server=Server('fixed-reviewed-source', on_list_tools=list_tools, on_call_tool=call_tool)
 async with stdio_server() as (read, write): await server.run(read, write, server.create_initialization_options())
if __name__ == '__main__': asyncio.run(main())
'''
    (out/'server.py').write_text(server,encoding='utf-8')
    (out/'README.md').write_text('Review all source, resources and adapter files before enabling. Explicitly declared flat package-data resources are copied; filename and private-key canary checks do not prove secrets are absent. Resources are provided as verified in-memory bytes through importlib.resources; filesystem paths and __file__ resources are unsupported. Declared dependencies: ' + json.dumps(dependencies) + '. Optional dependencies: ' + json.dumps(optional) + '. Install reviewed dependencies manually; none are installed automatically. Calls run with full OS privileges, not in a sandbox. Hermes untrusted server trust requests a per-call gate according to host policy, which may be overridden. Hashes detect changes but cannot protect against hostile local users or adapter edits. Process-tree cleanup is best effort, not a sandbox: POSIX children can escape by creating a new session; OS identity, permissions, detached services and hostile local users remain outside its guarantees. Windows requires assignable job objects and NtResumeProcess; launch fails closed if unavailable. Conversion and listing do not execute source.\n',encoding='utf-8')
    return {'output_path':str(out),'files':sorted(str(p.relative_to(out)) for p in out.rglob('*') if p.is_file()), 'status':'partial: review source and verify runtime imports before use'}
