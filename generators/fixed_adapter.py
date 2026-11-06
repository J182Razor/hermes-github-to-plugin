from __future__ import annotations

import ast
import hashlib
import json
import re
import shutil
import tomllib
from pathlib import Path
import importlib.util

_spec = importlib.util.spec_from_file_location('_g2h_metadata_io', Path(__file__).parent.parent / 'metadata_io.py')
_metadata_io = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_metadata_io)
METADATA_LIMIT = 1024 * 1024


def _requirements(project, kind):
    if not isinstance(project, dict) or 'optional-dependencies' in project or 'dynamic' in project:
        raise ValueError('optional or dynamic dependencies are unsupported')
    requirements = project.get('dependencies', [])
    if not isinstance(requirements, list) or len(requirements) > 64:
        raise ValueError('invalid or excessive dependencies')
    if requirements and kind != 'cli':
        raise ValueError('declared dependencies supported only for single-file CLI')
    # Deliberately accept only numeric release comparisons: no URLs, extras,
    # markers, wildcard, pre/post/dev releases, arbitrary equality or epochs.
    pattern = r'[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?(?:[ \t]*(?:==|!=|<=|>=|<|>)[ \t]*[0-9]+(?:\.[0-9]+)*(?:[ \t]*,[ \t]*(?:==|!=|<=|>=|<|>)[ \t]*[0-9]+(?:\.[0-9]+)*)*)?'
    if any(not isinstance(x, str) or len(x) > 256 or not re.fullmatch(pattern, x, re.ASCII) for x in requirements):
        raise ValueError('unsupported or malformed dependency requirement')
    return requirements


def generate(repo_path, out_dir, name, report):
    root = Path(repo_path)
    metadata = root / 'pyproject.toml'
    if metadata.is_symlink() or not metadata.is_file():
        raise ValueError('a non-symlink pyproject.toml is required')
    data = _metadata_io.toml_data(metadata, METADATA_LIMIT)
    project = data.get('project', {})
    kind = report['type']
    if kind == 'cli':
        scripts = project.get('scripts', {})
        if not isinstance(scripts, dict) or len(scripts) != 1:
            raise ValueError('exactly one project.scripts entry is required')
        entry = next(iter(scripts.values()))
    else:
        entry = data.get('tool', {}).get('github-to-hermes', {}).get('python-entrypoint')
    if isinstance(entry, str) and '.' in entry.split(':')[0]:
        import importlib.util
        spec = importlib.util.spec_from_file_location('_g2h_package_adapter', Path(__file__).with_name('package_adapter.py'))
        package_adapter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(package_adapter)
        return package_adapter.generate(repo_path, out_dir, name, report)
    requirements = _requirements(project, kind)
    if not isinstance(entry, str) or not re.fullmatch(r'[A-Za-z_]\w*:[A-Za-z_]\w*', entry, re.ASCII):
        raise ValueError('one explicit top-level module:function entrypoint is required')
    module, function = entry.split(':')
    source = root / (module + '.py')
    if source.is_symlink() or not source.is_file() or source.resolve().parent != root.resolve():
        raise ValueError('entrypoint must be a non-symlink top-level Python module')
    source_bytes = _metadata_io.read_bounded(source, 1024 * 1024)
    tree = ast.parse(source_bytes)
    definitions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == function]
    if len(definitions) != 1:
        raise ValueError('one synchronous top-level entrypoint required')
    decorated = bool(definitions[0].decorator_list)
    if kind == 'cli' and any(isinstance(n, ast.Name) and n.id == '__file__' for n in ast.walk(tree)):
        raise ValueError('single-file CLI using __file__ is unsupported: copied source changes file-resource semantics')
    if decorated:
        if kind != 'cli':
            raise ValueError('decorated entrypoint supported only for Click CLI')
        imports = [n for n in tree.body if isinstance(n, ast.Import) and any(a.name == 'click' and a.asname is None for a in n.names)]
        if len(imports) != 1 or not any(re.match(r'^click(?:\s|[<>=!]|$)', req, re.I) for req in requirements):
            raise ValueError('Click CLI requires unaliased import click and declared click dependency')
        groups = set()
        decorated_names = set()
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or not node.decorator_list:
                continue
            if node.name in decorated_names:
                raise ValueError('ambiguous decorated command binding')
            decorated_names.add(node.name)
            for dec in node.decorator_list:
                call = dec if isinstance(dec, ast.Call) else None
                attr = call.func if call else None
                if not isinstance(attr, ast.Attribute) or not isinstance(attr.value, ast.Name):
                    raise ValueError('unsupported Click decorator')
                owner, name_ = attr.value.id, attr.attr
                if owner == 'click' and name_ in ('command', 'group', 'option', 'argument'):
                    if node.name == function and name_ == 'group':
                        groups.add(function)
                elif owner in groups and name_ in ('command', 'group'):
                    if name_ == 'group': groups.add(node.name)
                else:
                    raise ValueError('unsupported or aliased Click decorator')
        root_decs = definitions[0].decorator_list
        if sum(isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and isinstance(d.func.value, ast.Name) and d.func.value.id == 'click' and d.func.attr in ('command', 'group') for d in root_decs) != 1:
            raise ValueError('entrypoint requires exactly one click.command or click.group')
        for n in ast.walk(tree):
            if isinstance(n, ast.Name) and n.id == 'click' and isinstance(n.ctx, (ast.Store, ast.Del)):
                raise ValueError('dynamic Click rebinding unsupported')
            if isinstance(n, (ast.Import, ast.ImportFrom)) and n not in imports and any((a.asname or a.name.split('.')[0]) == 'click' or a.name == '*' for a in n.names):
                raise ValueError('ambiguous Click import')
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in decorated_names and not node.decorator_list:
                    raise ValueError('ambiguous decorated command binding')
                continue
            if any(isinstance(n, ast.Name) and n.id in decorated_names and isinstance(n.ctx, (ast.Store, ast.Del)) for n in ast.walk(node)):
                raise ValueError('dynamic command rebinding unsupported')
            if any(isinstance(n, (ast.Import, ast.ImportFrom)) and any((a.asname or a.name.split('.')[0]) in decorated_names for a in n.names) for n in ast.walk(node)):
                raise ValueError('dynamic command import unsupported')
            if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name) and n.func.value.id in decorated_names and n.func.attr == 'add_command' for n in ast.walk(node)):
                raise ValueError('dynamic command registration unsupported')
    args = definitions[0].args
    parameters = args.posonlyargs + args.args + args.kwonlyargs
    if kind != 'cli' and parameters and (any(arg.annotation is not None for arg in parameters) or len(args.defaults) != len(args.posonlyargs) + len(args.args) or any(default is None for default in args.kw_defaults)):
        import importlib.util
        spec = importlib.util.spec_from_file_location('_g2h_package_adapter', Path(__file__).with_name('package_adapter.py'))
        package_adapter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(package_adapter)
        return package_adapter.generate(repo_path, out_dir, name, report, single_file=True)
    if not decorated and (len(args.defaults) != len(args.posonlyargs) + len(args.args) or any(default is None for default in args.kw_defaults)):
        raise ValueError('entrypoint must be callable without arguments')
    for default in list(args.defaults) + list(args.kw_defaults):
        try:
            ast.literal_eval(default)
        except (ValueError, TypeError, SyntaxError):
            raise ValueError('literal defaults required')
    for node in tree.body:
        if node is definitions[0]:
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == function:
                raise ValueError('ambiguous entrypoint binding')
            continue
        if any(isinstance(n, (ast.Subscript, ast.Attribute)) and isinstance(n.ctx, (ast.Store, ast.Del)) for n in ast.walk(node)):
            raise ValueError('ambiguous dynamic entrypoint binding')
        if any(isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)) and n.id == function for n in ast.walk(node)):
            raise ValueError('ambiguous entrypoint binding')
        if any(isinstance(n, (ast.Import, ast.ImportFrom)) and any(a.name == '*' or (a.asname or a.name.split('.')[0]) == function for a in n.names) for n in ast.walk(node)):
            raise ValueError('ambiguous entrypoint binding')
    prefix = 'g2h_' + re.sub(r'[^a-z0-9_]', '_', name.lower())[:32] + '_' + hashlib.sha256(name.encode()).hexdigest()[:12]
    tool = prefix + '_run'
    out = Path(out_dir) / name
    out.mkdir(parents=True, exist_ok=False)
    try:
        (out / 'source.py').write_bytes(source_bytes)
        digest = hashlib.sha256(source_bytes).hexdigest()
        manifest_name = name if len(name) <= 64 and re.fullmatch(r'(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', name) else 'g2h-' + hashlib.sha256(name.encode()).hexdigest()[:24]
        plugin = {'$schema':'https://agent-plugins.org/schemas/1.0.0/plugin.schema.json', 'name':manifest_name, 'version':'1.0.0', 'description':'Fixed reviewed entrypoint MCP adapter', 'extensions':{'com.nousresearch.hermes':{'servers':{prefix:{'trust':'untrusted'}}}}}
        plugin['extensions']['com.nousresearch.hermes']['declaredRequirements'] = requirements
        (out / 'plugin.json').write_text(json.dumps(plugin, indent=2), encoding='utf-8')
        config = {'$schema':'https://agent-plugins.org/schemas/1.0.0/mcp.schema.json', 'mcpServers':{prefix:{'type':'stdio', 'command':'python', 'args':['${PLUGIN_ROOT}/server.py']}}}
        (out / 'mcp.json').write_text(json.dumps(config, indent=2), encoding='utf-8')
        code = r'''import hashlib
import json
import os
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

SOURCE = str(Path(__file__).with_name("source.py"))
PROGRAM = r"""import sys, json, re, base64, importlib.metadata as metadata
requirements = json.loads(sys.argv[3])
for req in requirements:
    match = re.fullmatch(r"([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)(.*)", req)
    name, specs = match.groups()
    try:
        installed = metadata.version(name)
    except metadata.PackageNotFoundError:
        sys.exit("missing required distribution: " + req)
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", installed):
        sys.exit("unsupported installed version for " + req + ": " + installed)
    actual = tuple(map(int, installed.split('.')))
    for op, value in re.findall(r"(==|!=|<=|>=|<|>)\s*([0-9]+(?:\.[0-9]+)*)", specs):
        expected = tuple(map(int, value.split('.')))
        size = max(len(actual), len(expected))
        a, b = actual + (0,) * (size-len(actual)), expected + (0,) * (size-len(expected))
        if not {"==": a == b, "!=": a != b, "<=": a <= b, ">=": a >= b, "<": a < b, ">": a > b}[op]:
            sys.exit("dependency version mismatch: " + req + " (installed " + installed + ")")
payload = json.loads(sys.stdin.buffer.read())
b = base64.b64decode(payload['source'])
fn, path = sys.argv[1:3]
sys.argv = [path, *payload['argv']]
ns = {'__name__':'_fixed_source', '__file__':path}
exec(compile(b, path, 'exec'), ns)
r = ns[fn]()
""" + ("sys.exit(r)" if KIND == "cli" else "print(json.dumps(r))")

def handle(args, **kwargs):
    if not isinstance(args, dict) or (not CLICK and args):
        return json.dumps({"success": False, "error": "arguments are unsupported"})
    argv = args.get('argv', [])
    if CLICK and (set(args) - {'argv'} or not isinstance(argv, list) or len(argv) > 64 or any(not isinstance(x, str) or '\0' in x or len(x) > 4096 for x in argv) or sum(len(x.encode('utf-8', 'surrogatepass')) for x in argv) > 16384):
        return json.dumps({"success": False, "error": "invalid or excessive argv"})
    try:
        path = Path(SOURCE)
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('source must be a non-symlink regular file')
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as stream:
            actual = os.fstat(stream.fileno())
            if not stat.S_ISREG(actual.st_mode) or getattr(actual, 'st_file_attributes', 0) & 0x400 or (info.st_dev, info.st_ino) != (actual.st_dev, actual.st_ino):
                raise ValueError('source must be a non-symlink regular file')
            source = stream.read(1024 * 1024 + 1)
        if len(source) > 1024 * 1024:
            raise ValueError('source file too large')
        if hashlib.sha256(source).hexdigest() != DIGEST:
            return json.dumps({"success": False, "error": "source digest mismatch"})
        import base64
        payload = json.dumps({'source': base64.b64encode(source).decode('ascii'), 'argv': argv}).encode('utf-8')
        returncode, stdout, stderr = _run_contained([sys.executable, "-I", "-c", PROGRAM, FUNCTION, SOURCE, json.dumps(REQUIREMENTS)], payload, cancel=kwargs.get('cancel'))
        return json.dumps({"success": returncode == 0, "exit_code": returncode, "output": stdout.decode("utf-8", "replace"), "stderr": stderr.decode("utf-8", "replace"), "error": stderr.decode("utf-8", "replace") if returncode else ""})
    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc)})
'''
        (out / 'adapter.py').write_text('KIND = ' + repr(kind) + '\nCLICK = ' + repr(decorated) + '\nFUNCTION = ' + repr(function) + '\nTOOL = ' + repr(tool) + '\nTOOLSET = ' + repr(prefix) + '\nDIGEST = ' + repr(digest) + '\nREQUIREMENTS = ' + repr(requirements) + '\n' + Path(__file__).with_name('contained_runtime.py.txt').read_text(encoding='utf-8') + '\n' + code, encoding='utf-8')
        server = '''import asyncio
import json
import threading
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types
import adapter

async def list_tools(ctx, params):
    schema = {"type":"object", "properties": {"argv": {"type":"array", "items":{"type":"string", "maxLength":4096}, "maxItems":64}} if adapter.CLICK else {}, "additionalProperties":False}
    return types.ListToolsResult(tools=[types.Tool(name=adapter.TOOL, description="Run reviewed source with full OS privileges", inputSchema=schema, annotations=types.ToolAnnotations(readOnlyHint=False))])

async def call_tool(ctx, params):
    if params.name != adapter.TOOL:
        return types.CallToolResult(content=[types.TextContent(type="text", text="unknown tool")], isError=True)
    cancel = threading.Event()
    task = asyncio.create_task(asyncio.to_thread(adapter.handle, params.arguments or {}, cancel=cancel))
    try:
        result = json.loads(await asyncio.shield(task))
    except asyncio.CancelledError:
        cancel.set()
        raise
    return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(result))], isError=not result["success"])

async def main():
    server = Server("fixed-reviewed-source", on_list_tools=list_tools, on_call_tool=call_tool)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())
'''
        (out / 'server.py').write_text(server, encoding='utf-8')
        (out / 'README.md').write_text('Review source.py and all adapter files before enabling or sharing; source is copied without secret scanning. Requires a Python interpreter named python on PATH with the Python MCP SDK 2.x already installed, plus any imports used by the source. No dependencies are installed automatically. Hermes untrusted server trust requests its per-call gate for write-capable tools under the configured host policy; users can override or bypass this policy. A call runs the fixed entrypoint with full OS access. Python -I is not a security sandbox. Digest verification detects source changes but does not protect against concurrent local mutation, edits to the adapter or hostile local users. Process-tree cleanup is best effort, not a sandbox: POSIX children can escape by creating a new session; OS identity, permissions, detached services and hostile local users remain outside its guarantees. Windows requires assignable job objects and NtResumeProcess; launch fails closed if unavailable. Conversion and listing do not execute the source.\n', encoding='utf-8')
        with (out / 'README.md').open('a', encoding='utf-8') as readme:
            readme.write('\nDeclared requirements (operator must manually provision in the worker interpreter; never installed automatically):\n' + ''.join('- ' + req + '\n' for req in requirements) + ('- None declared.\n' if not requirements else '') + 'Only numeric release versions and simple comparisons are supported; non-numeric installed versions fail closed. Metadata checks do not verify package integrity, transitive requirements or importability.\n')
            if decorated:
                readme.write('\nClick support is limited to unaliased import click, @click.command()/@click.group(), @click.option()/@click.argument(), and statically declared @group.command()/@group.group() subcommands. The declared click dependency must already be installed. argv accepts at most 64 strings, 4096 characters each and 16384 UTF-8 bytes total; NUL is forbidden. Arguments are passed directly, never through a shell. Direct __file__ references are rejected because the source is copied; other indirect resource assumptions are not verified. Review them manually. The original Click aliases example uses __file__ for reads and writes and is therefore unsupported.\n')
    except Exception:
        # Leave partial output for manual inspection; path-based deletion can race.
        raise
    return {'output_path': str(out), 'files': sorted(p.name for p in out.iterdir()), 'status':'partial: review source and verify runtime imports before use'}
