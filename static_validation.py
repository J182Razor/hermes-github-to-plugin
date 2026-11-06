"""Bounded, non-executing subset of Hermes plugin validation.

Not an OS sandbox and not robust against concurrent filesystem mutation. It does
not inspect source, skills, dependencies, registration, or all loader semantics.
"""
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

_spec = importlib.util.spec_from_file_location('_g2h_metadata_io', Path(__file__).parent / 'metadata_io.py')
_metadata_io = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_metadata_io)
read_bounded = _metadata_io.read_bounded

PLUGIN_SCHEMA = 'https://agent-plugins.org/schemas/1.0.0/plugin.schema.json'
MCP_SCHEMA = 'https://agent-plugins.org/schemas/1.0.0/mcp.schema.json'
MAX_FILE = 128 * 1024
MAX_TOTAL = 256 * 1024
_NAME = re.compile(r'^(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$')


def _components(path):
    path = Path(os.path.abspath(path))
    parts = list(reversed((path, *path.parents)))
    for part in parts:
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('link or reparse point in path')
    return path


def _scoped(root, text):
    if not isinstance(text, str) or not text or '\\' in text or '\x00' in text:
        raise ValueError('invalid scoped path')
    for prefix in ('${PLUGIN_ROOT}/', './'):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    p = PurePosixPath(text)
    if p.is_absolute() or ':' in text or any(x in ('.', '..') for x in text.split('/')):
        raise ValueError('invalid scoped path')
    # Paths are checked when present. Missing targets are reported instead of guessed.
    target = root.joinpath(*p.parts)
    _components(target)
    return target


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate object key')
        result[key] = value
    return result


def _json(raw):
    return json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('invalid JSON constant')))


def _yaml(raw):
    try:
        import yaml
    except ImportError as exc:
        raise ValueError('PyYAML is required for native static validation; install only with operator authorization') from exc
    class StrictLoader(yaml.SafeLoader):
        def compose_node(self, parent, index):
            if self.check_event(yaml.AliasEvent):
                raise ValueError('YAML aliases are not supported')
            return super().compose_node(parent, index)
        def construct_mapping(self, node, deep=False):
            result = {}
            for key_node, value_node in node.value:
                key = self.construct_object(key_node, deep=deep)
                if not isinstance(key, str) or key in result:
                    raise ValueError('invalid or duplicate YAML key')
                result[key] = self.construct_object(value_node, deep=deep)
            return result
    return yaml.load(raw.decode('utf-8-sig'), Loader=StrictLoader)


def _strings(value):
    return isinstance(value, list) and all(isinstance(x, str) and bool(x) for x in value)


def validate_static(plugin_root):
    """Return an explicit subset report; never load candidate code or run a CLI."""
    report = {'ok': False, 'static_only': True, 'runtime_validation': 'not run',
              'checks': [], 'errors': [], 'warnings': [
                  'Incomplete static coverage; not a safety assessment or equivalent to Hermes validation.',
                  'Source, dependencies, registration, skills, and full extension semantics are not checked.',
                  'Concurrent filesystem mutation is not prevented; path checks are not an OS sandbox.',
                  'Full doctor and runtime validation require explicit operator authorization and may execute code or prepare dependencies.']}
    total = 0
    try:
        root = _components(plugin_root)
        if not root.is_dir():
            raise ValueError('plugin root must be a directory')
        def read(name):
            nonlocal total
            path = root / name
            _components(path)
            limit = min(MAX_FILE, MAX_TOTAL - total)
            if path.lstat().st_size > limit:
                raise ValueError('metadata file too large')
            raw = read_bounded(path, limit)
            total += len(raw)
            return raw
        native = os.path.lexists(root / 'plugin.yaml')
        portable = os.path.lexists(root / 'plugin.json')
        if native == portable:
            raise ValueError('expected exactly one native or portable manifest')
        if native:
            data = _yaml(read('plugin.yaml'))
            if not isinstance(data, dict):
                raise ValueError('native manifest must be a mapping')
            name = data.get('name')
            if not isinstance(name, str) or len(name) > 64 or not _NAME.fullmatch(name):
                raise ValueError('invalid native plugin name')
            version = data.get('manifest_version', 1)
            if type(version) is not int or version not in (1, 2):
                raise ValueError('unsupported native manifest version')
            for key in ('provides_tools', 'provides_hooks', 'requires_env', 'python_dependencies'):
                if key in data and not _strings(data[key]):
                    raise ValueError('invalid native list declaration')
            if 'config_schema' in data and (not isinstance(data['config_schema'], dict) or any(not isinstance(x, dict) for x in data['config_schema'].values())):
                raise ValueError('invalid native config schema')
            report['checks'].append('native manifest name, version and selected declarations')
        else:
            data = _json(read('plugin.json'))
            if not isinstance(data, dict) or data.get('$schema') != PLUGIN_SCHEMA:
                raise ValueError('invalid portable plugin schema')
            name = data.get('name')
            if not isinstance(name, str) or len(name) > 64 or not _NAME.fullmatch(name):
                raise ValueError('invalid portable plugin name')
            for field in ('version', 'description', 'homepage', 'repository', 'license'):
                if field in data and not isinstance(data[field], str):
                    raise ValueError('invalid portable metadata field')
            if 'keywords' in data and not _strings(data['keywords']):
                raise ValueError('invalid portable keywords')
            author = data.get('author', {})
            if not isinstance(author, dict) or set(author) - {'name', 'email', 'url'} or not all(isinstance(x, str) for x in author.values()):
                raise ValueError('invalid portable author')
            ext = data.get('extensions', {})
            if not isinstance(ext, dict) or any(not isinstance(v, dict) for v in ext.values()):
                raise ValueError('invalid portable extensions')
            declarations = ext.get('com.nousresearch.hermes', {}).get('servers', {})
            if not isinstance(declarations, dict) or any(not isinstance(v, dict) for v in declarations.values()):
                raise ValueError('invalid server declarations')
            for declaration in declarations.values():
                if 'trust' in declaration and declaration['trust'] not in ('full', 'untrusted'):
                    raise ValueError('invalid server trust policy')
                if set(declaration) - {'trust'}:
                    raise ValueError('unsupported server declaration policy')
            servers = {}
            if os.path.lexists(root / 'mcp.json'):
                mcp = _json(read('mcp.json'))
                if not isinstance(mcp, dict) or set(mcp) != {'$schema', 'mcpServers'} or mcp['$schema'] != MCP_SCHEMA or not isinstance(mcp['mcpServers'], dict):
                    raise ValueError('invalid MCP manifest schema')
                servers = mcp['mcpServers']
                for key, server in servers.items():
                    if not isinstance(key, str) or not key or not isinstance(server, dict):
                        raise ValueError('invalid MCP server declaration')
                    if server.get('type') != 'stdio':
                        raise ValueError('only stdio servers are covered by static validation')
                    if set(server) - {'type', 'command', 'args', 'env', 'cwd'}:
                        raise ValueError('invalid stdio fields')
                    command = server.get('command')
                    if not isinstance(command, str) or not command or '\x00' in command:
                        raise ValueError('invalid stdio command')
                    if command.startswith('./'):
                        _scoped(root, command)
                    elif any(c.isspace() or c in '/\\' for c in command) or command in ('.', '..'):
                        raise ValueError('invalid stdio command')
                    args, env = server.get('args', []), server.get('env', {})
                    if not isinstance(args, list) or not all(isinstance(x, str) for x in args) or not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
                        raise ValueError('invalid stdio arguments or environment')
                    if any(k.upper() in ('PLUGIN_ROOT', 'PLUGIN_DATA') for k in env):
                        raise ValueError('reserved environment variable')
                    if 'cwd' in server:
                        if isinstance(server['cwd'], str) and (server['cwd'] == '${PLUGIN_DATA}' or server['cwd'].startswith('${PLUGIN_DATA}/')):
                            raise ValueError('PLUGIN_DATA cwd is unsupported without an approved data root')
                        _scoped(root, server['cwd'])
                    for arg in args:
                        if arg.startswith('${PLUGIN_ROOT}/'):
                            _scoped(root, arg)
            if set(declarations) - set(servers):
                raise ValueError('server declaration has no matching MCP server')
            report['checks'].append('portable manifest, stdio declarations and selected scoped paths')
        report['ok'] = True
    except (OSError, ValueError, TypeError, RecursionError, UnicodeError) as exc:
        # Never include parser messages or manifest-derived content.
        safe = str(exc) if type(exc) is ValueError and str(exc) in {
            'PyYAML is required for native static validation; install only with operator authorization',
            'metadata file too large', 'link or reparse point in path', 'invalid scoped path',
            'only stdio servers are covered by static validation', 'invalid server trust policy',
            'unsupported server declaration policy', 'PLUGIN_DATA cwd is unsupported without an approved data root'} else 'invalid, unreadable, oversized, or unsupported plugin metadata'
        report['errors'].append(safe)
    except Exception:
        report['errors'].append('invalid, unreadable, oversized, or unsupported plugin metadata')
    return report
