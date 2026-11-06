"""Optional host validation. The subprocess is not a security boundary."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys
import tempfile

MAX_ENTRIES = 2000
MAX_BYTES = 16 * 1024 * 1024
DEADLINE = 60
WARNING = ('Plugin registration code executes with the same OS privileges as Hermes. '
           'No sandbox, network restriction, or process-tree guarantee. Normal Hermes '
           'approval policy (including YOLO, off, cached or unattended policy) may bypass '
           'a fresh human prompt. Same-user adversaries and plugin code can alter the '
           'snapshot or report; results are not tamper-proof.')


def snapshot(source, destination=None):
    """Copy or hash a bounded regular-file tree; names and bytes define the digest."""
    source = Path(source)
    digest = hashlib.sha256()
    count = total = 0
    stack = [(source, '')]
    while stack:
        directory, prefix = stack.pop()
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('unsupported directory')
        if prefix:
            name = prefix.encode('utf-8')
            digest.update(b'D' + len(name).to_bytes(4, 'big') + name)
            if destination is not None:
                (destination / prefix).mkdir()
        with os.scandir(directory) as entries:
            batch = []
            for entry in entries:
                count += 1
                if count > MAX_ENTRIES:
                    raise ValueError('too many entries')
                if ':' in entry.name or entry.name in ('.', '..'):
                    raise ValueError('unsupported filename')
                batch.append(entry.name)
        for name in sorted(batch, reverse=True):
            rel = prefix + '/' + name if prefix else name
            item = directory / name
            info = item.lstat()
            if getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError('reparse point')
            if stat.S_ISDIR(info.st_mode):
                stack.append((item, rel))
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('unsupported file')
            total += info.st_size
            if total > MAX_BYTES:
                raise ValueError('too many bytes')
            # Read only up to the remaining budget, including a growth sentinel.
            with item.open('rb') as stream:
                data = stream.read(info.st_size + 1)
            if len(data) != info.st_size:
                raise ValueError('file changed')
            encoded = rel.encode('utf-8')
            digest.update(b'F' + len(encoded).to_bytes(4, 'big') + encoded + len(data).to_bytes(8, 'big') + hashlib.sha256(data).digest())
            if destination is not None:
                (destination / rel).write_bytes(data)
    return digest.hexdigest()


# Stdout/stderr are discarded: candidate logs never become the tool result.
_WORKER = r'''
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
root, output, kind = Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]
result = {'validate_plugin_dir': 'failed', 'doctor_plugin': 'not run'}
try:
    from hermes_cli.plugin_validate import validate_plugin_dir
    report = validate_plugin_dir(root)
    result['validate_plugin_dir'] = 'passed' if report.ok is True else 'failed'
    if kind == 'native':
        try:
            from hermes_cli.plugin_dev import doctor_plugin
            doctor = doctor_plugin(root)
            result['doctor_plugin'] = 'passed' if doctor.ok is True else 'failed'
            if not doctor.ok and any(isinstance(f.message, str) and f.level == 'error' and
                    f.message.startswith('hook callback ') and
                    f.message.endswith(' must accept **kwargs for forward compatibility')
                    for f in doctor.findings):
                result['reason'] = 'hook_requires_kwargs'
        except Exception:
            result['doctor_plugin'] = 'failed'
    else:
        result['doctor_plugin'] = 'unsupported'
except Exception:
    pass
output.write_text(json.dumps(result), encoding='utf-8')
'''


def validate_runtime(path, detector):
    result = {'success': False, 'runtime_validation': 'not run', 'warning': WARNING}
    private = None
    stage = 'path'
    code = 'invalid_path'
    messages = {
        'invalid_path': 'Candidate path is invalid or does not exist.',
        'not_directory': 'Candidate path is not a directory.',
        'no_manifest': 'No supported manifest was found.',
        'ambiguous_manifest': 'Multiple supported manifests were found.',
        'source_unsupported': 'Source tree contains an unsupported entry.',
        'snapshot_failed': 'Snapshot failed or exceeded its limits; review the source tree locally.',
        'approval_denied': 'Runtime approval was denied.',
        'approval_unavailable': 'Runtime approval was unavailable.',
        'host_import_missing': 'Hermes host validation is unavailable.',
        'snapshot_changed': 'Snapshot changed after approval.',
        'timeout': 'Host validation timed out.',
        'worker_failed': 'Host validation worker failed; review locally.',
        'worker_report_invalid': 'Host validation returned an inconsistent or invalid report.',
        'validator_failed': 'Host validator failed; review locally.',
        'doctor_failed': 'Host doctor failed; review locally.',
        'unknown': 'Runtime validation failed; local operator review is recommended.',
    }
    def fail(error_code):
        result.update(error_code=error_code, error=messages[error_code], stage=stage)
    try:
        source = detector.reject_link_components(path)
        if not source.exists():
            fail('invalid_path'); return result
        if not source.is_dir():
            fail('not_directory'); return result
        stage = 'snapshot'
        code = 'snapshot_failed'
        parent = detector.reject_link_components(tempfile.gettempdir())
        if source == parent or source in parent.parents:
            fail('source_unsupported'); return result
        if os.name == 'nt':
            private = detector._windows_private_clone_dir(parent)
        else:
            private = tempfile.mkdtemp(prefix='g2h_', dir=str(parent))
            info = os.lstat(private)
            detector._clone_identities[private] = (info.st_dev, info.st_ino)
        private = Path(private)
        copy = private / 'plugin'
        copy.mkdir()
        try:
            digest = snapshot(source, copy)
        except ValueError as exc:
            if str(exc) in ('unsupported directory', 'unsupported filename', 'reparse point', 'unsupported file'):
                fail('source_unsupported'); return result
            raise
        # Determine kind from the exact copied bytes covered by the approval digest.
        manifests = [name for name in ('plugin.yaml', 'plugin.yml', 'plugin.json') if (copy / name).is_file()]
        if len(manifests) != 1:
            fail('ambiguous_manifest' if manifests else 'no_manifest'); return result
        kind = 'portable' if manifests[0] == 'plugin.json' else 'native'
        reason = ('Run host validate_plugin_dir and, for native plugins, doctor_plugin on a '
                  'bounded snapshot (max 2000 entries, 16 MiB); registration may run more '
                  'than once. SHA256 snapshot: ' + digest + '. ' + WARNING)
        stage = 'approval'
        code = 'approval_unavailable'
        from tools.approval import request_tool_approval
        approval = request_tool_approval('validate_plugin', reason,
                                         rule_key='validate_plugin:runtime:' + secrets.token_hex(16))
        if not isinstance(approval, dict) or approval.get('approved') is not True:
            fail('approval_denied'); return result
        stage = 'host_import'
        code = 'host_import_missing'
        try:
            import hermes_cli
        except ImportError:
            fail('host_import_missing'); return result
        host = str(Path(hermes_cli.__file__).resolve().parent.parent)
        stage = 'snapshot'
        code = 'snapshot_changed'
        home = private / 'home'
        home.mkdir()
        env = {key: os.environ[key] for key in ('PATH', 'SYSTEMROOT', 'SystemRoot', 'WINDIR', 'COMSPEC', 'PATHEXT') if key in os.environ}
        env.update({'HOME': str(home), 'USERPROFILE': str(home), 'HERMES_HOME': str(home),
                    'TMPDIR': str(private), 'TMP': str(private), 'TEMP': str(private),
                    'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1'})
        output = private / 'report.json'
        if snapshot(copy) != digest:
            fail('snapshot_changed'); return result
        stage = 'worker'
        code = 'worker_failed'
        result['runtime_validation'] = 'failed or incomplete'
        try:
            child = subprocess.run([sys.executable, '-B', '-c', _WORKER, host, str(copy), str(output), kind],
                                   cwd=str(private), env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=DEADLINE)
        except subprocess.TimeoutExpired:
            fail('timeout'); return result
        if child.returncode != 0:
            fail('worker_failed'); return result
        code = 'worker_report_invalid'
        if not output.is_file() or output.stat().st_size > 1024:
            fail(code); return result
        report = json.loads(output.read_text(encoding='utf-8'))
        if not isinstance(report, dict):
            fail(code); return result
        validation = report.get('validate_plugin_dir')
        doctor = report.get('doctor_plugin')
        valid_doctor = ('passed', 'failed') if kind == 'native' else ('unsupported',)
        if validation not in ('passed', 'failed') or doctor not in valid_doctor:
            fail(code); return result
        result.update({'runtime_validation': 'completed', 'snapshot_sha256': digest,
                       'checks': {'validate_plugin_dir': validation, 'doctor_plugin': doctor},
                       'success': validation == 'passed' and (doctor == 'passed' if kind == 'native' else doctor == 'unsupported')})
        if validation == 'failed':
            fail('validator_failed')
        elif doctor == 'failed':
            fail('doctor_failed')
            if report.get('reason') == 'hook_requires_kwargs':
                result['reason_code'] = 'hook_requires_kwargs'
                result['error'] = 'Host doctor failed: hook callbacks must accept **kwargs for forward compatibility.'
        return result
    except Exception as exc:
        if stage == 'approval':
            fail('approval_unavailable')
        elif stage == 'path' and isinstance(exc, (ValueError, TypeError, OSError)):
            fail('invalid_path')
        elif stage == 'snapshot' and isinstance(exc, (ValueError, OSError)):
            fail('snapshot_failed' if code != 'snapshot_changed' else 'snapshot_changed')
        elif stage == 'worker' and isinstance(exc, (OSError, ValueError)):
            fail(code)
        else:
            fail('unknown')
        return result
    finally:
        if private is not None:
            try:
                detector._cleanup_clone(str(private))
            except Exception:
                result['success'] = False
                result['cleanup_warning'] = 'Temporary snapshot cleanup failed; local operator review is required.'
