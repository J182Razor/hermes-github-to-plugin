from __future__ import annotations
import json
import re
import shutil
from pathlib import Path


def generate(repo_path, out_dir, name, report):
    import importlib.util
    spec = importlib.util.spec_from_file_location('_g2h_deferred_mcp', Path(__file__).with_name('deferred_mcp.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.generate(repo_path, out_dir, name, report)
    root = Path(repo_path)
    config = root / 'mcp.json'
    if config.is_symlink() or not config.is_file():
        raise ValueError('a non-symlink mcp.json is required')
    data = json.loads(config.read_text(encoding='utf-8'))
    servers = data.get('mcpServers') if isinstance(data, dict) else None
    if not isinstance(servers, dict) or not servers:
        raise ValueError('mcpServers must be nonempty')
    converted, files = {}, {}
    for server, cfg in servers.items():
        if not re.fullmatch(r'[A-Za-z0-9_-]+', server) or not isinstance(cfg, dict) or set(cfg) - {'type', 'command', 'args', 'env'}:
            raise ValueError('unsupported MCP server configuration')
        if cfg.get('type', 'stdio') != 'stdio' or cfg.get('command') not in ('python', 'python3', 'node'):
            raise ValueError('only local python or node stdio scripts are supported')
        args = cfg.get('args')
        if not isinstance(args, list) or len(args) != 1 or not isinstance(args[0], str) or not re.fullmatch(r'[A-Za-z0-9_-]+\.(py|js|mjs)', args[0]):
            raise ValueError('one top-level script argument required')
        script = root / args[0]
        if script.is_symlink() or not script.is_file():
            raise ValueError('server script must be a real file')
        env = cfg.get('env', {})
        if not isinstance(env, dict) or any(not re.fullmatch(r'[A-Z_][A-Z0-9_]*', k) or v != '${' + k + '}' for k, v in env.items()):
            raise ValueError('environment values must be same-name placeholders, not credentials')
        files[args[0]] = script
        converted[server] = {'type': 'stdio', 'command': cfg['command'], 'args': ['${PLUGIN_ROOT}/source/' + args[0]], 'env': env}
    out = Path(out_dir) / name
    out.mkdir(parents=True, exist_ok=False)
    try:
        (out / 'source').mkdir()
        for filename, source in files.items():
            shutil.copyfile(source, out / 'source' / filename)
        plugin = {'$schema':'https://agent-plugins.org/schemas/1.0.0/plugin.schema.json', 'name': name, 'version':'1.0.0', 'description':'Generated MCP package; review before enabling', 'extensions':{'com.nousresearch.hermes':{'servers':{k:{'trust':'untrusted'} for k in converted}}}}
        (out / 'plugin.json').write_text(json.dumps(plugin, indent=2), encoding='utf-8')
        (out / 'mcp.json').write_text(json.dumps({'$schema':'https://agent-plugins.org/schemas/1.0.0/mcp.schema.json', 'mcpServers':converted}, indent=2), encoding='utf-8')
        (out / 'README.md').write_text('Review copied scripts for embedded secrets before sharing, and review code and configuration before enabling. Scripts are copied without secret scanning and may contain arbitrary credentials. Enabling starts untrusted code with your privileges. Install dependencies manually only after review. Only top-level server scripts are copied; provide any required dependencies separately.\n', encoding='utf-8')
    except Exception:
        # Leave partial output for manual inspection; path-based deletion can race.
        raise
    return {'output_path':str(out), 'files':sorted(str(p.relative_to(out)) for p in out.rglob('*') if p.is_file()), 'status':'partial: review and dependencies required'}
