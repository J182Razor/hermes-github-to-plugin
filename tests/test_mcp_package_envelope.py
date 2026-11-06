import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from generators import mcp_safe


def envelope(package):
    return base64.b64encode(json.dumps(package, separators=(',', ':')).encode('utf-8'))


class PackageEnvelopeTests(unittest.TestCase):
    def test_boundaries_and_no_output_on_overflow(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); src = root / 'src'; src.mkdir()
            pkg = src / 'example'; pkg.mkdir()
            (pkg / '__init__.py').write_bytes(b'')
            (pkg / 'server.py').write_bytes(b'pass\n')
            (src / 'mcp.json').write_text(json.dumps({'mcpServers': {'fixture': {'command': 'python', 'args': ['-m', 'example.server']}}}))
            def package(size):
                return {'module': 'example.server', 'files': {
                    'example/__init__.py': '',
                    'example/server.py': base64.b64encode(b'pass\n').decode(),
                    'example/helper.py': base64.b64encode(b'#' + b'x' * size).decode(),
                }}
            lo, hi = 0, 65536
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if len(envelope(package(mid))) <= 65536: lo = mid
                else: hi = mid - 1
            self.assertEqual(len(envelope(package(lo))), 65536)
            self.assertGreater(len(envelope(package(lo + 1))), 65536)
            (pkg / 'helper.py').write_bytes(b'#' + b'x' * lo)
            mcp_safe.generate(src, root / 'out', 'valid', {})
            (pkg / 'helper.py').write_bytes(b'#' + b'x' * (lo + 1))
            with self.assertRaisesRegex(ValueError, '65536'):
                mcp_safe.generate(src, root / 'out', 'invalid', {})
            self.assertFalse((root / 'out' / 'invalid').exists())

    def test_frame_overhead_bound_including_digests(self):
        # The decoded envelope JSON is at most 49152 bytes. Its module,
        # paths and source encodings together cannot exceed that budget.
        # Even if every byte were JSON-escaped to six bytes in both package
        # and digest path keys, plus 64 SHA-256 values and generous framing,
        # this bound stays well below the 1 MiB transport cap.
        conservative_bound = 2 * 6 * (65536 * 3 // 4) + 64 * 80 + 1024
        self.assertLess(conservative_bound, 1048576)
        from mcp.types import JSONRPCNotification
        paths = [('p' * 500) + str(i) + '.py' for i in range(64)]
        package = {'module': 'example.server', 'files': {p: '' for p in paths}}
        digests = {p: hashlib.sha256(b'').hexdigest() for p in paths}
        self.assertLessEqual(len(envelope(package)), 65536)
        payload = {'package': package, 'digests': digests}
        notice = JSONRPCNotification(jsonrpc='2.0', method='notifications/reviewed-source', params=payload)
        self.assertLess(len(notice.model_dump_json(by_alias=True, exclude_none=True).encode()) + 1, 1048576)
        request = dict(payload, name='increment', arguments={})
        self.assertLess(len(json.dumps(request).encode()) + 1, 1048576)
