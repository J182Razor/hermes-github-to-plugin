import hashlib
import os
from pathlib import Path
import tempfile
import unittest

from generators import package_mcp
from metadata_io import read_bounded


class ConsolePackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ['TMPDIR'])
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'src'
        self.pkg = self.root / 'console_fixture'
        self.pkg.mkdir(parents=True)
        self.marker = Path(self.tmp.name) / 'executed'
        self.init = self.pkg / '__init__.py'
        self.init.write_text('from .helper import value\n' +
                             'open(' + repr(str(self.marker)) + ', "w").write("executed")\n' +
                             'def main(arg=1, *, option=None):\n    return value\n', encoding='utf-8')
        (self.pkg / 'helper.py').write_text('value = 1\n', encoding='utf-8')

    def collect(self, module='console_fixture', function='main'):
        return package_mcp.collect(self.root, module, read_bounded, console_function=function)

    def test_collects_package_without_execution(self):
        files, digests = self.collect()
        self.assertEqual(set(files), {'console_fixture/__init__.py', 'console_fixture/helper.py'})
        self.assertEqual(digests, {p: hashlib.sha256(data).hexdigest() for p, data in files.items()})
        self.assertFalse(self.marker.exists())

    def test_direct_dynamic_binding_overwrite_rejected_without_execution(self):
        for mutation in ('globals().update(main=object())',
                         "setattr(sys.modules[__name__], 'main', object())",
                         "delattr(sys.modules[__name__], 'main')"):
            with self.subTest(mutation=mutation):
                self.init.write_text('import sys\nopen(' + repr(str(self.marker)) + ', "w").write("executed")\ndef main(): pass\n' + mutation + '\n', encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'binding'):
                    self.collect()
                self.assertFalse(self.marker.exists())

    def test_single_relative_reexport_is_collected_without_execution(self):
        self.init.write_text('from .helper import main\n', encoding='utf-8')
        (self.pkg / 'helper.py').write_text('def main(): pass\n', encoding='utf-8')
        files, _ = self.collect()
        self.assertIn('console_fixture/helper.py', files)
        self.assertFalse(self.marker.exists())

    def test_deferred_back_import_is_not_an_import_time_cycle(self):
        self.init.write_text('from .helper import run as main\n', encoding='utf-8')
        (self.pkg / 'helper.py').write_text('def run():\n    from . import main\n', encoding='utf-8')
        self.collect()
        self.assertFalse(self.marker.exists())

    def test_reexport_alias_and_ambiguous_bindings(self):
        helper = self.pkg / 'helper.py'
        self.init.write_text('from .helper import run as main\n', encoding='utf-8')
        helper.write_text('def run(): pass\n', encoding='utf-8')
        self.collect()
        cases = [
            ('from .helper import run as main\nmain = None\n', 'def run(): pass\n'),
            ('from .helper import run as main\nfrom .helper import run as main\n', 'def run(): pass\n'),
            ('from .helper import *\n', 'def main(): pass\n'),
            ('from .helper import run as main\n', 'async def run(): pass\n'),
            ('from .helper import run as main\n', '@staticmethod\ndef run(): pass\n'),
            ('from .helper import run as main\n', 'def run(x): pass\n'),
            ('from .helper import run as main\n', 'def run(): pass\nrun = None\n'),
            ('from .helper import run as main\n', 'def run(): pass\nglobals().update(run=None)\n'),
            ('from .helper import run as main\n', 'from . import main\ndef run(): pass\n'),
            ('from .helper import run as main\n', 'from . import main as run\n'),
            ('from ..external import main\n', 'def main(): pass\n'),
        ]
        for init, body in cases:
            with self.subTest(init=init, body=body):
                self.init.write_text(init, encoding='utf-8')
                helper.write_text(body, encoding='utf-8')
                with self.assertRaises(ValueError):
                    self.collect()
                self.assertFalse(self.marker.exists())

    def test_invalid_entrypoints(self):
        for source in ('x = 1', 'async def main(): pass', '@staticmethod\ndef main(): pass',
                       'def main(x): pass', 'def main(*, x): pass',
                       'def main(): pass\ndef main(): pass',
                       'def main(): pass\nmain = None',
                       'def main(): pass\nfrom os import path as main',
                       'def main(): pass\nif True:\n    del main',
                       'def main(): pass\nfrom os import *'):
            with self.subTest(source=source):
                self.init.write_text(source, encoding='utf-8')
                with self.assertRaises(ValueError):
                    self.collect()

    def test_invalid_identifiers_and_legacy_mode(self):
        for module, function in [('console_fixture.helper', 'main'), ('class', 'main'),
                                 ('console_fixture', 'class'), ('console_fixture', 'máin'),
                                 ('console_fixture', ''), ('console_fixture', 'main.extra')]:
            with self.subTest(module=module, function=function), self.assertRaises(ValueError):
                self.collect(module, function)
        with self.assertRaises(ValueError):
            package_mcp.collect(self.root, 'console_fixture', read_bounded)
        self.assertTrue(package_mcp.collect(self.root, 'console_fixture.helper', read_bounded))

    def test_exact_runtime_envelope_boundary(self):
        from generators.package_envelope import package_envelope_size
        import base64
        function = 'f' * 300
        requirements = ['mcp>=1', 'some-distribution>=1']
        (self.pkg / 'helper.py').unlink()
        prefix = ('def ' + function + '(): pass\n#').encode()
        path = 'console_fixture/__init__.py'
        def size(n):
            return package_envelope_size({'module':'console_fixture','files':{path:base64.b64encode(prefix + b'x'*n).decode()},'entrypoint':function}, requirements)
        n = next(n for n in range(36000, 38000) if size(n) == 65536 and size(n+1) > 65536)
        for padding, accepted in ((n,True),(n+1,False)):
            self.init.write_bytes(prefix + b'x'*padding)
            if accepted:
                package_mcp.collect(self.root, 'console_fixture', read_bounded, console_function=function, requirements=requirements)
            else:
                with self.assertRaisesRegex(ValueError, 'envelope'):
                    package_mcp.collect(self.root, 'console_fixture', read_bounded, console_function=function, requirements=requirements)
        self.assertFalse(self.marker.exists())

    def test_console_envelope_includes_entrypoint_and_requirements(self):
        function = 'f' * 300
        self.init.write_text('def ' + function + '(): pass\n#' + 'x' * 36790, encoding='utf-8')
        (self.pkg / 'helper.py').unlink()
        with self.assertRaisesRegex(ValueError, 'envelope'):
            package_mcp.collect(self.root, 'console_fixture', read_bounded, console_function=function, requirements=['mcp>=1', 'some-distribution>=1'])

    def test_envelope_limit(self):
        (self.pkg / 'helper.py').write_text('value = 1\n#' + 'x' * 60000, encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'envelope'):
            self.collect()

    def test_symlink_rejected(self):
        link = self.pkg / 'link.py'
        try:
            link.symlink_to(self.pkg / 'helper.py')
        except OSError as exc:
            self.skipTest('symlink creation unavailable: ' + str(exc))
        with self.assertRaises(ValueError):
            self.collect()


if __name__ == '__main__':
    unittest.main()
