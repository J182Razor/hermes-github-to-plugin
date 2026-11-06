from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path


def generate(repo_path, out_dir, name, report):
    root = Path(repo_path)
    candidates = [root / n for n in ('openapi.json', 'swagger.json', 'openapi.yaml', 'openapi.yml', 'swagger.yaml', 'swagger.yml') if (root / n).is_file()]
    if len(candidates) != 1 or candidates[0].is_symlink():
        raise ValueError('exactly one non-symlink OpenAPI file required')
    with candidates[0].open('rb') as source:
        raw = source.read(1048577)
    if len(raw) > 1048576:
        raise ValueError('OpenAPI document too large')
    text = raw.decode('utf-8')
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if not isinstance(key, str) or key in result:
                raise ValueError('non-string or duplicate mapping key')
            result[key] = value
        return result
    if candidates[0].suffix == '.json':
        data = json.loads(text, object_pairs_hook=unique_pairs)
    else:
        try:
            import yaml
        except ImportError as exc:
            raise ValueError('YAML support requires PyYAML') from exc
        try:
            if any(isinstance(token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken)) for token in yaml.scan(text)):
                raise ValueError('YAML anchors and aliases are unsupported')
            class UniqueLoader(yaml.SafeLoader):
                def construct_mapping(self, node, deep=False):
                    pairs = self.construct_pairs(node, deep=deep)
                    result = {}
                    for key, value in pairs:
                        if not isinstance(key, (str, int)) or key in result:
                            raise ValueError('invalid or duplicate YAML mapping key')
                        result[key] = value
                    return result
            data = yaml.load(text, Loader=UniqueLoader)
        except yaml.YAMLError as exc:
            raise ValueError('invalid OpenAPI YAML') from exc
    if (not isinstance(data, dict) or not isinstance(data.get('openapi'), str)
            or not re.fullmatch(r'3\.0\.\d+', data['openapi']) or not isinstance(data.get('paths'), dict)):
        raise ValueError('OpenAPI 3.0 paths required')
    server_url, server_path = '', ''
    if 'servers' in data:
        from urllib.parse import urlsplit
        servers = data['servers']
        if not isinstance(servers, list) or len(servers) != 1 or not isinstance(servers[0], dict) or set(servers[0]) - {'url', 'description'}:
            raise ValueError('exactly one literal OpenAPI root server required')
        server_url = servers[0].get('url')
        if (not isinstance(server_url, str) or len(server_url) > 2048
                or not re.fullmatch(r'https://(?:[A-Za-z0-9.-]+|\[[0-9A-Fa-f:.]+\])(?::[0-9]+)?(?:/[A-Za-z0-9._~/-]*)?', server_url)):
            raise ValueError('unsafe OpenAPI root server URL')
        parsed_server = urlsplit(server_url)
        import ipaddress
        host = parsed_server.hostname or ''
        try:
            if parsed_server.netloc.startswith('['):
                ipaddress.IPv6Address(host)
            elif re.fullmatch(r'[0-9.]+', host):
                ipaddress.IPv4Address(host)
            else:
                if len(host) > 253 or any(not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label) for label in host.split('.')):
                    raise ValueError()
                for label in host.split('.'):
                    if label.lower().startswith('xn--'):
                        if label.encode('ascii').decode('idna').encode('idna').decode('ascii').lower() != label.lower():
                            raise ValueError()
        except (ValueError, UnicodeError) as exc:
            raise ValueError('unsafe OpenAPI root server host') from exc
        try:
            port = parsed_server.port
        except ValueError as exc:
            raise ValueError('unsafe OpenAPI root server port') from exc
        if (not parsed_server.hostname or port == 0 or '//' in parsed_server.path
                or parsed_server.path.endswith('/')
                or any(part in ('.', '..') for part in parsed_server.path.split('/'))):
            raise ValueError('unsafe OpenAPI root server path')
        server_path = parsed_server.path
    if set(data) - {'openapi', 'info', 'paths', 'tags', 'externalDocs', 'components', 'security', 'servers'}:
        raise ValueError('unsupported OpenAPI root field')
    components = data.get('components', {})
    if (not isinstance(components, dict) or set(components) - {'schemas', 'parameters', 'responses', 'requestBodies', 'securitySchemes'}
            or any(not isinstance(section, dict) for section in components.values())):
        raise ValueError('unsupported OpenAPI components')

    # Expand only same-document component pointers, with one shared budget for
    # the complete expanded tree. No URI decoding, array indices or I/O.
    budget = [100000, 4 * 1024 * 1024]
    def expand(value, active=(), depth=0):
        if depth > 64:
            raise ValueError('OpenAPI reference depth exceeded')
        budget[0] -= 1
        if isinstance(value, str):
            budget[1] -= len(value.encode('utf-8'))
        if budget[0] < 0 or budget[1] < 0:
            raise ValueError('OpenAPI expanded size exceeded')
        if isinstance(value, dict):
            if len(value) > 4096:
                raise ValueError('OpenAPI mapping too large')
            if '$ref' in value:
                ref = value['$ref']
                if len(value) != 1 or not isinstance(ref, str) or not ref.startswith('#/') or '%' in ref:
                    raise ValueError('invalid OpenAPI reference')
                tokens = ref[2:].split('/')
                if (len(tokens) < 3 or len(tokens) > 64 or tokens[0] != 'components'
                        or tokens[1] not in ('schemas', 'parameters', 'responses', 'requestBodies')
                        or (tokens[1] == 'requestBodies' and (len(tokens) != 3 or not tokens[2]))
                        or any(re.search(r'~(?![01])', token) for token in tokens)):
                    raise ValueError('invalid OpenAPI pointer')
                tokens = tuple(token.replace('~1', '/').replace('~0', '~') for token in tokens)
                if tokens in active:
                    raise ValueError('circular OpenAPI reference')
                target = data
                for token in tokens:
                    if not isinstance(target, dict) or token not in target:
                        raise ValueError('dangling OpenAPI pointer')
                    target = target[token]
                if not isinstance(target, dict):
                    raise ValueError('OpenAPI reference target must be an object')
                return expand(target, active + (tokens,), depth + 1)
            result = {}
            for key, item in value.items():
                if isinstance(key, str):
                    budget[1] -= len(key.encode('utf-8'))
                if budget[1] < 0:
                    raise ValueError('OpenAPI expanded size exceeded')
                result[key] = expand(item, active, depth + 1)
            return result
        if isinstance(value, list):
            if len(value) > 4096:
                raise ValueError('OpenAPI array too large')
            return [expand(item, active, depth + 1) for item in value]
        return value
    # Resolve only the operation tree; unused component definitions may be recursive.
    # Pointer lookup above continues to use the original document.
    paths = expand(data['paths'])
    schemes = data.get('components', {}).get('securitySchemes', {})
    used_schemes = set()
    def security(value):
        if value == []: return None
        if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict) or len(value[0]) != 1:
            raise ValueError('unsupported security requirements')
        key, scopes = next(iter(value[0].items()))
        if not isinstance(key, str) or key not in schemes or scopes != []:
            raise ValueError('unsupported security requirement')
        scheme = schemes[key]
        if not isinstance(scheme, dict):
            raise ValueError('invalid security scheme')
        if scheme.get('type') == 'http' and set(scheme) <= {'type', 'scheme', 'bearerFormat', 'description'} and str(scheme.get('scheme', '')).lower() == 'bearer':
            auth = ('bearer', 'Authorization')
        elif scheme.get('type') == 'apiKey' and set(scheme) <= {'type', 'in', 'name', 'description'} and scheme.get('in') == 'header' and isinstance(scheme.get('name'), str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9-]{0,63}', scheme['name']) and scheme['name'].lower() not in {'authorization', 'host', 'content-type', 'content-length', 'connection', 'cookie', 'proxy-authorization', 'accept', 'accept-language', 'x-request-id', 'x-trace-id'}:
            auth = ('apiKey', scheme['name'])
        else:
            raise ValueError('unsupported security scheme')
        used_schemes.add(key)
        if len(used_schemes) > 1:
            raise ValueError('multiple security schemes unsupported')
        return auth
    global_security = data.get('security', [])
    operations, used = [], set()
    prefix = 'g2h_' + re.sub(r'[^a-z0-9_]', '_', name.lower())[:24] + '_' + hashlib.sha256(name.encode()).hexdigest()[:12]
    for path, methods in paths.items():
        if not isinstance(path, str) or not re.fullmatch(r'/[A-Za-z0-9_./~{}-]*', path) or '..' in path or not isinstance(methods, dict):
            raise ValueError('invalid path')
        for method, operation in methods.items():
            if method not in ('get', 'post', 'put', 'patch', 'delete') or not isinstance(operation, dict) or set(operation) - ({'operationId', 'summary', 'description', 'responses', 'tags', 'deprecated', 'parameters', 'security'} | ({'requestBody'} if method != 'get' else set())):
                raise ValueError('unsupported operation')
            auth = security(operation.get('security', global_security))
            body = None
            if 'requestBody' in operation:
                request = operation['requestBody']
                if not isinstance(request, dict) or set(request) - {'required', 'content', 'description'} or type(request.get('required', False)) is not bool or not isinstance(request.get('content'), dict) or set(request['content']) != {'application/json'}:
                    raise ValueError('request body requires supported JSON object content')
                media = request['content']['application/json']
                if not isinstance(media, dict) or set(media) != {'schema'}:
                    raise ValueError('unsupported media')
                body = media['schema']
                if not isinstance(body, dict) or set(body) - {'type', 'properties', 'required', 'additionalProperties'} or body.get('type') != 'object' or not isinstance(body.get('properties'), dict) or type(body.get('additionalProperties', True)) is not bool:
                    raise ValueError('unsupported body schema')
                required = body.get('required', [])
                if not isinstance(required, list) or any(not isinstance(x, str) or x not in body['properties'] for x in required) or len(set(required)) != len(required):
                    raise ValueError('invalid body requirements')
                for key, value in body['properties'].items():
                    if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', key) or not isinstance(value, dict) or set(value) != {'type'} or value['type'] not in ('string', 'integer', 'number', 'boolean'):
                        raise ValueError('unsupported body property')
                body = dict(body, **{'x-g2h-required': request.get('required', False)})
            parameters = operation.get('parameters', [])
            if not isinstance(parameters, list):
                raise ValueError('parameters must be a list')
            if ('//' in path or any(part in ('.', '..') for part in path.split('/'))):
                raise ValueError('unsafe path')
            seen = set()
            for param in parameters:
                if not isinstance(param, dict) or set(param) - {'name', 'in', 'required', 'schema', 'description', 'style', 'explode'}:
                    raise ValueError('unsupported parameter')
                key, location, schema = param.get('name'), param.get('in'), param.get('schema')
                if (not isinstance(key, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]*', key) or (location != 'header' and '-' in key) or key.lower() in seen or key.lower() == 'base_url'
                        or location not in ('path', 'query', 'header') or not isinstance(schema, dict)
                        or set(schema) != {'type'} or schema['type'] not in ('string', 'integer', 'number', 'boolean')
                        or ('required' in param and not isinstance(param['required'], bool))
                        or (location == 'path' and param.get('required') is not True)
                        or param.get('style', 'form' if location == 'query' else 'simple') != ('form' if location == 'query' else 'simple')
                        or ('explode' in param and type(param['explode']) is not bool)
                        or (location == 'header' and key.lower() not in {'accept', 'accept-language', 'x-request-id', 'x-trace-id'})):
                    raise ValueError('unsupported or invalid parameter')
                seen.add(key.lower())
            if body is not None and 'body' in seen:
                raise ValueError('body parameter collision')
            placeholders = re.findall(r'\{([A-Za-z][A-Za-z0-9_]*)\}', path)
            if re.sub(r'\{[A-Za-z][A-Za-z0-9_]*\}', '', path).find('{') != -1 or '}' in re.sub(r'\{[A-Za-z][A-Za-z0-9_]*\}', '', path) or set(placeholders) != {p['name'] for p in parameters if p['in'] == 'path'}:
                raise ValueError('path placeholders must match path parameters')
            op = operation.get('operationId', '')
            if not isinstance(op, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', op):
                raise ValueError('each operation requires a simple operationId')
            tool = prefix + '_' + op.lower()
            if len(tool) > 64 or tool in used:
                raise ValueError('operation name collision or too long')
            used.add(tool)
            operations.append((tool, path, parameters, method.upper(), body, auth))
    if not operations:
        raise ValueError('no supported operations')
    import importlib.util
    spec = importlib.util.spec_from_file_location('_g2h_rest_portable', Path(__file__).with_name('rest_portable.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.generate(out_dir, name, operations, server_url, server_path)
