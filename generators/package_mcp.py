"""Collect a small, statically declared Python package without executing it."""
import ast
import base64
import hashlib
import importlib.util
import json
import keyword
import os
import re
import stat
import sys
from pathlib import Path

_IDENTIFIER = re.compile(r'[A-Za-z_][A-Za-z0-9_]*\Z')


def require_fastmcp_evidence(files, module, function):
    """Recognize a narrow source pattern, not a runtime or security guarantee."""
    def fail():
        raise ValueError('ambiguous FastMCP server evidence')

    tree = ast.parse(files[module + '/__init__.py'])
    if any(isinstance(n, ast.Raise) for n in tree.body):
        fail()
    definitions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == function]
    if not definitions:
        exports = [(n, a) for n in tree.body if isinstance(n, ast.ImportFrom)
                   and n.level == 1 and n.module and '.' not in n.module
                   for a in n.names if a.name == function and a.asname is None]
        if len(exports) != 1 or len(exports[0][0].names) != 1:
            fail()
        path = module + '/' + exports[0][0].module + '.py'
        if path not in files:
            fail()
        tree = ast.parse(files[path])
        if any(isinstance(n, ast.Raise) for n in tree.body):
            fail()
        definitions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == function]
    if len(definitions) != 1:
        fail()
    imports = [n for n in tree.body if isinstance(n, ast.ImportFrom)
               and n.level == 0 and n.module == 'mcp.server.fastmcp'
               and len(n.names) == 1 and n.names[0].name == 'FastMCP'
               and n.names[0].asname is None]
    dotted = [n for n in tree.body if isinstance(n, ast.Import) and len(n.names) == 1
              and n.names[0].name == 'mcp.server.fastmcp' and n.names[0].asname is None]
    if len(imports) + len(dotted) != 1:
        fail()
    constructor = 'FastMCP' if imports else 'mcp.server.fastmcp.FastMCP'
    instances = [n for n in tree.body if isinstance(n, ast.Assign) and len(n.targets) == 1
                 and isinstance(n.targets[0], ast.Name) and isinstance(n.value, ast.Call)
                 and ast.unparse(n.value.func) == constructor]
    if len(instances) != 1:
        fail()
    instance = instances[0].targets[0].id
    protected = {instance, 'FastMCP' if imports else 'mcp'}
    if len(protected) != 2:
        fail()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)) and isinstance(node.value, ast.Name) and node.value.id in protected:
            fail()
        if isinstance(node, ast.Name) and node.id in protected and isinstance(node.ctx, (ast.Store, ast.Del)) and node is not instances[0].targets[0]:
            fail()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in protected:
            fail()
        if isinstance(node, ast.arg) and node.arg in protected:
            fail()
        if isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name in protected:
            fail()
        if isinstance(node, ast.MatchMapping) and node.rest in protected:
            fail()
        if isinstance(node, (ast.Global, ast.Nonlocal)) and protected.intersection(node.names):
            fail()
        if isinstance(node, (ast.Import, ast.ImportFrom)) and node not in imports + dotted:
            if any(a.name == '*' or (a.asname or a.name.split('.')[0]) in protected for a in node.names):
                fail()
        if isinstance(node, (ast.Attribute, ast.Subscript)) and isinstance(node.ctx, (ast.Store, ast.Del)):
            fail()
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {'globals', 'locals', 'vars', 'setattr', 'delattr'}:
            fail()
    calls = [n for n in definitions[0].body if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
             and isinstance(n.value.func, ast.Attribute) and n.value.func.attr == 'run'
             and isinstance(n.value.func.value, ast.Name) and n.value.func.value.id == instance]
    body = definitions[0].body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        body = body[1:]
    if len(calls) != 1 or body != calls:
        fail()
    call = calls[0].value
    if call.args or (call.keywords and not (
            len(call.keywords) == 1 and call.keywords[0].arg == 'transport'
            and isinstance(call.keywords[0].value, ast.Constant)
            and call.keywords[0].value.value == 'stdio')):
        fail()


def collect(root, module, read_bounded, *, console_function=None, requirements=None, identity=None):
    parts = module.split('.')
    if ((len(parts) < 2 if console_function is None else len(parts) != 1)
            or any(not _IDENTIFIER.fullmatch(p) or keyword.iskeyword(p) for p in parts)):
        raise ValueError('invalid Python module name')
    if console_function is not None and (not isinstance(console_function, str)
            or not _IDENTIFIER.fullmatch(console_function) or keyword.iskeyword(console_function)):
        raise ValueError('invalid console function name')
    if parts[0] in sys.stdlib_module_names or parts[0] in sys.modules:
        raise ValueError('package name conflicts with runtime module')
    package = root / parts[0]
    files = {}
    entry_count = 0

    def visit(directory):
        nonlocal entry_count
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('package directories must not be links or reparse points')
        found_init = False
        with os.scandir(directory) as entries:
          for entry in entries:
            entry_count += 1
            if entry_count > 64:
                raise ValueError('package exceeds 64 entries')
            path = directory / entry.name
            found_init |= entry.name == '__init__.py'
            name = entry.name.casefold()
            if (name in {'.git', '.env', '.envrc', '.ssh', '.aws', '.gnupg', '.netrc', '.npmrc', '.pypirc', '.docker', 'credentials', 'credential', 'auth', 'secrets', 'secret', 'id_rsa', 'id_ed25519'}
                    or name.startswith(('.git.', '.env.', 'id_rsa.', 'id_ed25519.'))
                    or re.search(r'(^|[._-])(credentials?|auth|secrets?|tokens?|private[._-]?key|api[._-]?key)([._-]|$)', name)
                    or name.endswith(('.pem', '.key', '.p12', '.pfx', '.keystore', '.jks'))):
                raise ValueError('sensitive package path unsupported')
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & 0x400:
                if not _IDENTIFIER.fullmatch(path.name):
                    raise ValueError('invalid package directory')
                visit(path)
            else:
                if not stat.S_ISREG(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                    raise ValueError('package links and special files forbidden')
                if path.suffix != '.py' or not _IDENTIFIER.fullmatch(path.stem):
                    raise ValueError('package resources are unsupported')
                if len(files) >= 64:
                    raise ValueError('package exceeds 64 files')
                raw = read_bounded(path, 1048576 - sum(map(len, files.values())))
                if b'-----BEGIN ' in raw and b'PRIVATE KEY-----' in raw:
                    raise ValueError('private key canary detected')
                relative = path.relative_to(root).as_posix()
                if relative in files:
                    raise ValueError('duplicate package path collision')
                files[relative] = raw
        if not found_init:
            raise ValueError('each package directory requires __init__.py')

    try:
        visit(package)
    except OSError as exc:
        raise ValueError('invalid package directory') from exc
    modules = {}
    canonical_names = set()
    for path in sorted(files):
        items = path[:-3].split('/')
        is_package = items[-1] == '__init__'
        name = '.'.join(items[:-1] if is_package else items)
        # Reject case aliases even on case-sensitive hosts: generated packages
        # can be reviewed and executed on a different filesystem.
        canonical = name.casefold()
        if canonical in canonical_names:
            raise ValueError('package module identity collision')
        canonical_names.add(canonical)
        modules[name] = (path, is_package)
    if module not in modules or modules[module][1] != (console_function is not None):
        raise ValueError('entry module must be a Python file or verified console package')
    trees = {}
    symbols = {}
    for name, (path, _) in modules.items():
        try:
            trees[name] = ast.parse(files[path], filename=path)
        except (SyntaxError, ValueError) as exc:
            raise ValueError('invalid Python package source') from exc
        declared = set()
        for statement in trees[name].body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                declared.add(statement.name)
            elif isinstance(statement, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
                for target in targets:
                    declared.update(n.id for n in ast.walk(target) if isinstance(n, ast.Name))
            elif isinstance(statement, (ast.Import, ast.ImportFrom)):
                declared.update(a.asname or (a.name.split('.')[0] if isinstance(statement, ast.Import) else a.name) for a in statement.names if a.name != '*')
        symbols[name] = declared
    if console_function is not None:
        tree = trees[module]
        definitions = [node for node in tree.body
                       if isinstance(node, ast.FunctionDef) and node.name == console_function]
        exports = [(node, alias) for node in tree.body
                   if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module
                   for alias in node.names if (alias.asname or alias.name) == console_function]
        if not definitions and len(exports) == 1:
            export, alias = exports[0]
            target = module + '.' + export.module
            if ('.' in export.module or target not in modules or modules[target][1]
                    or len(export.names) != 1):
                raise ValueError('entrypoint re-export must target one package module')
            origin_tree = trees[target]
            # Function bodies are deferred; decorators, defaults and annotations
            # execute during import and remain conservatively inspected.
            class ImportTime(ast.NodeVisitor):
                def __init__(self): self.imports = []
                def visit_ImportFrom(self, node): self.imports.append(node)
                def visit_FunctionDef(self, node):
                    for part in [*node.decorator_list, node.args, node.returns]:
                        if part is not None: self.visit(part)
                visit_AsyncFunctionDef = visit_FunctionDef
                def visit_Lambda(self, node): self.visit(node.args)
            scan = ImportTime()
            scan.visit(origin_tree)
            if any(((node.level == 1 and node.module is None)
                    or (node.level == 0 and node.module == module))
                   and any(a.name in {console_function, '*'} for a in node.names)
                   for node in scan.imports):
                raise ValueError('cyclic entrypoint import')
            origin_name = alias.name
            origin_defs = [node for node in origin_tree.body
                           if isinstance(node, ast.FunctionDef) and node.name == origin_name]
            if len(origin_defs) != 1 or origin_defs[0].decorator_list:
                raise ValueError('one undecorated synchronous top-level entrypoint required')
            checks = [(tree, console_function, export), (origin_tree, origin_name, origin_defs[0])]
            definition = origin_defs[0]
        elif len(definitions) == 1 and not definitions[0].decorator_list:
            definition = definitions[0]
            checks = [(tree, console_function, definition)]
        else:
            raise ValueError('one undecorated synchronous top-level entrypoint required')
        args = definition.args
        if len(args.defaults) != len(args.posonlyargs) + len(args.args) or any(default is None for default in args.kw_defaults):
            raise ValueError('entrypoint must be callable without arguments')
        for checked_tree, checked_name, binding in checks:
          for node in checked_tree.body:
            if node is binding:
                continue
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name == checked_name:
                    raise ValueError('ambiguous entrypoint binding')
                continue
            if any(isinstance(n, ast.Call) and (
                    (isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Call)
                     and isinstance(n.func.value.func, ast.Name) and n.func.value.func.id in {'globals', 'locals', 'vars'}
                     and n.func.attr in {'update', 'setdefault', 'pop', 'popitem', 'clear', '__setitem__', '__delitem__'})
                    or (isinstance(n.func, ast.Name) and n.func.id in {'setattr', 'delattr'}
                        and len(n.args) >= 2 and isinstance(n.args[1], ast.Constant)
                        and n.args[1].value == checked_name)) for n in ast.walk(node)):
                raise ValueError('ambiguous dynamic entrypoint binding')
            if any(isinstance(n, (ast.Subscript, ast.Attribute)) and isinstance(n.ctx, (ast.Store, ast.Del)) for n in ast.walk(node)):
                raise ValueError('ambiguous dynamic entrypoint binding')
            if any(isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)) and n.id == checked_name for n in ast.walk(node)):
                raise ValueError('ambiguous entrypoint binding')
            if any(isinstance(n, (ast.Import, ast.ImportFrom)) and any(a.name == '*' or (a.asname or a.name.split('.')[0]) == checked_name for a in n.names) for n in ast.walk(node)):
                raise ValueError('ambiguous entrypoint binding')
    for name, (path, is_package) in modules.items():
        tree = trees[name]
        package_name = name if is_package else name.rpartition('.')[0]
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in {'__file__', '__import__', 'eval', 'exec'}:
                raise ValueError('dynamic imports or __file__ are unsupported')
            if isinstance(node, ast.Attribute) and node.attr in {'import_module', 'find_spec', 'spec_from_file_location', 'SourceFileLoader'}:
                raise ValueError('dynamic imports are unsupported')
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {'open', 'Path'} and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str) and not Path(node.args[0].value).is_absolute():
                raise ValueError('relative resources are unsupported')
            if isinstance(node, ast.ImportFrom) and node.level:
                try:
                    target = importlib.util.resolve_name('.' * node.level + (node.module or ''), package_name)
                except (ImportError, ValueError) as exc:
                    raise ValueError('relative import escapes package') from exc
                if target not in modules:
                    raise ValueError('unknown package import: ' + target)
                for alias in node.names:
                    if alias.name == '*' or (alias.name not in symbols[target] and target + '.' + alias.name not in modules):
                        raise ValueError('ambiguous or unknown package import: ' + alias.name)
            if isinstance(node, ast.ImportFrom) and not node.level and node.module in modules:
                for alias in node.names:
                    if alias.name == '*' or (alias.name not in symbols[node.module] and node.module + '.' + alias.name not in modules):
                        raise ValueError('ambiguous or unknown package import: ' + alias.name)
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else ([node.module or ''] if not node.level else [])
                for target in names:
                    top = target.split('.')[0]
                    if top == parts[0] and target not in modules:
                        raise ValueError('unknown package import: ' + target)
                    if top != parts[0] and ((root / (top + '.py')).exists() or (root / top).exists()):
                        raise ValueError('adjacent imports are unsupported: ' + top)
    encoded = {path: base64.b64encode(raw).decode('ascii') for path, raw in files.items()}
    spec = importlib.util.spec_from_file_location('_g2h_envelope', Path(__file__).with_name('package_envelope.py'))
    envelope_helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(envelope_helper)
    package_payload = {'module': module, 'files': encoded}
    if console_function is not None:
        package_payload['entrypoint'] = console_function
        if identity is not None:
            package_payload['identity'] = identity
    if envelope_helper.package_envelope_size(package_payload, requirements) > 65536:
        raise ValueError('package exceeds 65536 byte envelope cap')
    return files, {path: hashlib.sha256(raw).hexdigest() for path, raw in files.items()}
