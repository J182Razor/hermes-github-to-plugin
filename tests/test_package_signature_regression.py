import tempfile
import unittest
import os
from pathlib import Path
from generators.package_adapter import generate

class SignatureRegression(unittest.TestCase):
    def test_non_utf8_source_executes_with_declared_encoding(self):
        import importlib.util, json
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); pkg = root / 'repo' / 'demo'; pkg.mkdir(parents=True)
            (pkg.parent / 'pyproject.toml').write_text('[project]\nname="demo"\nversion="1"\n[tool.github-to-hermes]\npython-entrypoint="demo.api:run"\n')
            (pkg / '__init__.py').write_text('')
            (pkg / 'api.py').write_bytes('# coding: latin-1\ndef run(): return "caf\u00e9"\n'.encode('latin-1'))
            result = generate(pkg.parent, root / 'out', 'demo', {'type':'python_pkg'})
            spec = importlib.util.spec_from_file_location('encoding_adapter', Path(result['output_path']) / 'adapter.py')
            module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
            result = json.loads(module.handle({}))
            self.assertTrue(result['success'], result)
            self.assertEqual(json.loads(result['output']), 'caf\u00e9')

    def test_resource_is_rejected_instead_of_silently_omitted(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); pkg = root / 'repo' / 'demo'; pkg.mkdir(parents=True)
            (pkg.parent / 'pyproject.toml').write_text('[project]\nname="demo"\nversion="1"\n[tool.github-to-hermes]\npython-entrypoint="demo.api:run"\n')
            (pkg / '__init__.py').write_text('')
            (pkg / 'api.py').write_text('def run(): return 1\n')
            (pkg / 'settings.json').write_text('{}')
            with self.assertRaises(ValueError):
                generate(pkg.parent, root / 'out', 'demo', {'type':'python_pkg'})

    def test_monkey_module_is_not_silently_filtered(self):
        import importlib.util, json
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); pkg = root / 'repo' / 'demo'; pkg.mkdir(parents=True)
            (pkg.parent / 'pyproject.toml').write_text('[project]\nname="demo"\nversion="1"\n[tool.github-to-hermes]\npython-entrypoint="demo.api:run"\n')
            (pkg / '__init__.py').write_text('')
            (pkg / 'api.py').write_text('from .monkey import value\ndef run(): return value()\n')
            (pkg / 'monkey.py').write_text('def value(): return 42\n')
            result = generate(pkg.parent, root / 'out', 'demo', {'type':'python_pkg'})
            spec = importlib.util.spec_from_file_location('monkey_adapter', Path(result['output_path']) / 'adapter.py')
            module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
            answer = json.loads(module.handle({}))
            self.assertTrue(answer['success'], answer)
            self.assertEqual(json.loads(answer['output']), 42)

    def test_rebinding_rejected_before_output(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); pkg = root / 'repo' / 'demo'; pkg.mkdir(parents=True)
            (pkg.parent / 'pyproject.toml').write_text('[project]\nname="demo"\nversion="1"\n[tool.github-to-hermes]\npython-entrypoint="demo.api:run"\n')
            (pkg / '__init__.py').write_text('')
            (pkg / 'api.py').write_text('def run(x: str): return x\nrun = lambda x: x\n')
            with self.assertRaises(ValueError):
                generate(pkg.parent, root / 'out', 'demo', {'type':'python_pkg'})
            self.assertFalse((root / 'out').exists())
