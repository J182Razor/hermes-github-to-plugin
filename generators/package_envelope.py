"""Canonical size accounting for reviewed package requests."""
import base64
import json


def package_envelope_size(package, requirements=None):
    fields = {'module': package['module'], 'files': package['files']}
    if 'entrypoint' in package:
        fields['entrypoint'] = package['entrypoint']
        if 'identity' in package:
            fields['identity'] = package['identity']
        fields['requirements'] = requirements
    return len(base64.b64encode(json.dumps(fields, separators=(',', ':')).encode()))
