import importlib.util
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from generators import fixed_adapter


class ProcessContainmentTests(unittest.TestCase):
    def test_overflow_kills_descendant(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); repo = root / 'repo'; repo.mkdir()
            marker = root / 'marker'
            (repo / 'pyproject.toml').write_text('[project]\nname="demo"\nversion="1"\n[tool.github-to-hermes]\npython-entrypoint="demo:run"\n')
            (repo / 'demo.py').write_text('import subprocess, sys, time\ndef run():\n subprocess.Popen([sys.executable, "-c", ' + repr('import time; from pathlib import Path; time.sleep(2); Path(' + repr(str(marker)) + ').write_text("ran")') + '])\n print("x" * 1100000, flush=True)\n time.sleep(5)\n')
            out = Path(fixed_adapter.generate(repo, root / 'out', 'demo', {'type':'python_pkg'})['output_path'])
            spec = importlib.util.spec_from_file_location('containment_adapter', out / 'adapter.py')
            adapter = importlib.util.module_from_spec(spec); spec.loader.exec_module(adapter)
            result = json.loads(adapter.handle({}))
            self.assertFalse(result['success']); self.assertIn('output limit', result['error'])
            time.sleep(3)
            self.assertFalse(marker.exists(), 'descendant survived overflow')

    def test_timeout_and_positive_control(self):
        import subprocess
        import sys
        namespace = {}
        exec((Path(__file__).parents[1] / 'generators' / 'contained_runtime.py.txt').read_text(), namespace)
        run = namespace['_run_contained']
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            marker = Path(tmp) / 'marker'
            writer = 'import time; from pathlib import Path; time.sleep(1); Path(' + repr(str(marker)) + ').write_text("ran")'
            spawn = 'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",' + repr(writer) + ']); time.sleep(5)'
            with self.assertRaisesRegex(ValueError, 'timeout'):
                run([sys.executable, '-c', spawn], b'', timeout=.3)
            time.sleep(1.5)
            self.assertFalse(marker.exists())
            subprocess.run([sys.executable, '-c', writer], check=True, timeout=5)
            self.assertTrue(marker.exists())

    @unittest.skipUnless(os.name == 'nt', 'Windows job objects')
    def test_job_failures_never_run_source(self):
        import ctypes
        import sys
        namespace = {}
        exec((Path(__file__).parents[1] / 'generators' / 'contained_runtime.py.txt').read_text(), namespace)
        real = ctypes.WinDLL
        from unittest.mock import MagicMock
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            marker = Path(tmp) / 'marker'
            command = [sys.executable, '-c', 'from pathlib import Path; Path(' + repr(str(marker)) + ').write_text("ran")']
            for failed in ('CreateJobObjectW', 'AssignProcessToJobObject'):
                kernel = real('kernel32', use_last_error=True)
                setattr(kernel, failed, MagicMock(return_value=0))
                with patch.object(ctypes, 'WinDLL', side_effect=lambda name, **kw: kernel if name == 'kernel32' else real(name, **kw)):
                    with self.assertRaises(OSError): namespace['_run_contained'](command, b'')
                time.sleep(.2)
                self.assertFalse(marker.exists(), failed)

    def test_cancel_kills_descendant(self):
        import sys
        import threading
        namespace = {}
        exec((Path(__file__).parents[1] / 'generators' / 'contained_runtime.py.txt').read_text(), namespace)
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            marker = Path(tmp) / 'marker'; ready = Path(tmp) / 'ready'
            writer = 'import time; from pathlib import Path; time.sleep(1.5); Path(' + repr(str(marker)) + ').write_text("ran")'
            spawn = 'import subprocess,sys,time; from pathlib import Path; subprocess.Popen([sys.executable,"-c",' + repr(writer) + ']); Path(' + repr(str(ready)) + ').write_text("ready"); time.sleep(5)'
            cancel = threading.Event(); outcomes = []
            def worker():
                try: namespace['_run_contained']([sys.executable, '-c', spawn], b'', cancel=cancel)
                except ValueError as exc: outcomes.append(str(exc))
            thread = threading.Thread(target=worker); thread.start()
            deadline = time.monotonic() + 4
            while not ready.exists() and time.monotonic() < deadline: time.sleep(.01)
            self.assertTrue(ready.exists())
            cancel.set(); thread.join(4)
            self.assertFalse(thread.is_alive()); self.assertEqual(outcomes, ['cancelled'])
            time.sleep(2); self.assertFalse(marker.exists())
