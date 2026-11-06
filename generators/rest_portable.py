from pathlib import Path
import hashlib
import json
import re

def generate(out_dir, name, operations, server_url='', server_path=''):
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', name) or '..' in name:
        raise ValueError('invalid name')
    server = 'g2h_' + hashlib.sha256(name.encode()).hexdigest()[:20]
    manifest_name = name if re.fullmatch(r'(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', name) else 'g2h-' + hashlib.sha256(name.encode()).hexdigest()[:24]
    out = Path(out_dir) / name
    out.mkdir(parents=True, exist_ok=False)
    (out/'plugin.json').write_text(json.dumps({'$schema':'https://agent-plugins.org/schemas/1.0.0/plugin.schema.json','name':manifest_name,'version':'1.0.0','description':'Reviewed OpenAPI adapter','extensions':{'com.nousresearch.hermes':{'servers':{server:{'trust':'untrusted'}}}}}, indent=2), encoding='utf-8')
    config = {'type':'stdio','command':'python','args':['${PLUGIN_ROOT}/server.py']}
    if any(op[5] for op in operations):
        config['env'] = {'G2H_API_CREDENTIAL':'${G2H_API_CREDENTIAL}'}
    (out/'mcp.json').write_text(json.dumps({'$schema':'https://agent-plugins.org/schemas/1.0.0/mcp.schema.json','mcpServers':{server:config}}, indent=2), encoding='utf-8')
    runtime = Path(__file__).with_name('contained_runtime.py.txt').read_text(encoding='utf-8')
    runtime = runtime.replace("    if cancel and cancel.is_set():\n        raise ValueError('cancelled')", "    deadline = time.monotonic() + timeout\n    if cancel and cancel.is_set():\n        raise ValueError('cancelled')", 1)
    runtime = runtime.replace('        deadline = time.monotonic() + timeout\n', '')
    runtime = runtime.replace("process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **opts)", "env = dict(os.environ); env.pop('G2H_API_CREDENTIAL', None)\n        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, **opts)")
    (out/'server.py').write_text('OPERATIONS = ' + repr(operations) + '\nSERVER_PATH = ' + repr(server_path) + '\n' + runtime + '\n' + Path(__file__).with_name('rest_portable_server.py.txt').read_text(encoding='utf-8'), encoding='utf-8')
    (out/'README.md').write_text(('Declared OpenAPI server (not automatically authorized): ' + (server_url or '(none)') + '. Its static path is always prepended to operation paths; configure base_url as an origin only. Allowlist entries use the effective root-prefixed path template: e.g. GET /api/v3/echo, not GET /echo. Existing policies are not upgraded automatically; review and explicitly update them when regenerating adapters. No-server paths are unchanged.\n') + 'Experimental. Review before use. Create openapi-authorization.json in the operator-controlled plugin data directory before starting Hermes, with keys base_url (exact origin) and operations (JSON array of "METHOD /path-template"). Protect that file and directory from model writes. GET, POST, PUT, PATCH and DELETE can have side effects; every call needs host untrusted approval unless host policy overrides it. Methods require separate exact allowlist entries. Only supported JSON object request bodies are accepted; unsupported content is rejected at conversion. The configured destination, including non-loopback, is explicitly operator authorized; DNS is trusted. For authenticated operations supply G2H_API_CREDENTIAL to the host environment; mcp.json contains only a placeholder. One bearer or header API-key scheme is supported. Direct and JSON-decoded credential echoes fail closed; transformed, split, encoded, partial or unrelated secrets cannot be reliably detected. Do not send credentials in arguments or responses. No proxies or redirects. The python executable on host PATH must contain the MCP SDK. Approval is not an OS sandbox.\n', encoding='utf-8')
    return {'output_path':str(out), 'files':sorted(p.name for p in out.iterdir()), 'status':'experimental; operator review and host approval required'}
