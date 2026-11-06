import ctypes
import threading
import tempfile
import time
from unittest.mock import patch
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
namespace = {}
exec((ROOT / 'generators/contained_runtime.py.txt').read_text(), namespace)
Worker = namespace['PersistentWorker']

@unittest.skipUnless(os.name == 'nt', 'Windows job required')
class PersistentWorkerLimits(unittest.TestCase):
    def test_spawn_failure(self):
        with patch.object(namespace['subprocess'], 'Popen', side_effect=OSError('synthetic spawn failure')):
            with self.assertRaisesRegex(OSError, 'synthetic spawn failure'):
                Worker(['unused'])

    def test_timeout_and_cancellation_close_worker(self):
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                worker = Worker([sys.executable, '-u', '-c', 'import time; time.sleep(20)'])
                event = threading.Event()
                if cancelled:
                    event.set()
                with self.assertRaisesRegex(ValueError, 'cancelled' if cancelled else 'timeout'):
                    worker.request(b'{}', timeout=.2, cancel=event)
                self.assertIsNone(worker.process)
                self.assertIsNone(worker.job)

    def test_eof_and_oversized_response_fail_closed(self):
        for code in ('pass', "import sys; sys.stdout.buffer.write(b'x'*1048577); sys.stdout.flush()"):
            with self.subTest(code=code):
                worker = Worker([sys.executable, '-u', '-c', code])
                with self.assertRaisesRegex(ValueError, 'invalid or oversized'):
                    worker.request(b'{}', timeout=5)
                self.assertIsNone(worker.job)

    def test_shutdown_kills_descendant_before_delayed_marker(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            marker = Path(tmp) / 'late'
            child = 'import time; from pathlib import Path; time.sleep(2); Path(%r).touch()' % str(marker)
            code = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',%r]); print('ready',flush=True); time.sleep(20)" % child
            worker = Worker([sys.executable, '-u', '-c', code])
            try:
                self.assertEqual(worker.request(b'{}', timeout=5).strip(), b'ready')
            finally:
                worker.close()
            time.sleep(2.5)
            self.assertFalse(marker.exists())

    def test_job_memory_limits_are_set(self):
        worker = Worker([sys.executable, '-c', 'import time; time.sleep(20)'])
        try:
            class Basic(ctypes.Structure):
                _fields_ = [('p', ctypes.c_longlong), ('j', ctypes.c_longlong), ('flags', ctypes.c_uint32), ('min', ctypes.c_size_t), ('max', ctypes.c_size_t), ('active', ctypes.c_uint32), ('affinity', ctypes.c_size_t), ('priority', ctypes.c_uint32), ('schedule', ctypes.c_uint32)]
            class Io(ctypes.Structure):
                _fields_ = [(str(i), ctypes.c_ulonglong) for i in range(6)]
            class Extended(ctypes.Structure):
                _fields_ = [('basic', Basic), ('io', Io), ('process', ctypes.c_size_t), ('job', ctypes.c_size_t), ('peakp', ctypes.c_size_t), ('peakj', ctypes.c_size_t)]
            info = Extended()
            query = worker.kernel.QueryInformationJobObject
            query.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
            query.restype = ctypes.c_int
            self.assertTrue(query(worker.job, 9, ctypes.byref(info), ctypes.sizeof(info), None))
            self.assertEqual(info.basic.flags & 0x300, 0x300)
            self.assertGreater(info.process, 0)
            self.assertGreaterEqual(info.job, info.process)
        finally:
            worker.close()

    def test_low_job_limit_rejects_bounded_allocation(self):
        class Basic(ctypes.Structure):
            _fields_ = [('p', ctypes.c_longlong), ('j', ctypes.c_longlong), ('flags', ctypes.c_uint32), ('min', ctypes.c_size_t), ('max', ctypes.c_size_t), ('active', ctypes.c_uint32), ('affinity', ctypes.c_size_t), ('priority', ctypes.c_uint32), ('schedule', ctypes.c_uint32)]
        class Io(ctypes.Structure):
            _fields_ = [(str(i), ctypes.c_ulonglong) for i in range(6)]
        class Extended(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', Io), ('process', ctypes.c_size_t), ('job', ctypes.c_size_t), ('peakp', ctypes.c_size_t), ('peakj', ctypes.c_size_t)]
        code = "import sys; print('ready',flush=True); sys.stdin.readline();\ntry:\n x=bytearray(80*1024*1024); print('allocated',flush=True)\nexcept MemoryError:\n print('MemoryError',flush=True)"
        worker = Worker([sys.executable, '-u', '-c', code])
        try:
            self.assertEqual(worker.process.stdout.readline().strip(), b'ready')
            query = worker.kernel.QueryInformationJobObject
            query.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
            query.restype = ctypes.c_int
            info = Extended()
            self.assertTrue(query(worker.job, 9, ctypes.byref(info), ctypes.sizeof(info), None))
            info.process = info.job = 64 * 1024 * 1024
            self.assertTrue(worker.kernel.SetInformationJobObject(worker.job, 9, ctypes.byref(info), ctypes.sizeof(info)))
            actual = Extended()
            self.assertTrue(query(worker.job, 9, ctypes.byref(actual), ctypes.sizeof(actual), None))
            self.assertEqual((actual.process, actual.job), (64 * 1024 * 1024,) * 2)
            self.assertEqual(actual.basic.flags & 0x300, 0x300)
            self.assertEqual(worker.request(b'go', timeout=10).strip(), b'MemoryError')
            self.assertEqual(2 + 2, 4)  # parent remains responsive
        finally:
            worker.close()

    def test_maximum_request_includes_newline(self):
        worker = Worker([sys.executable, '-u', '-c', 'import sys; sys.stdout.buffer.write(sys.stdin.buffer.readline()); sys.stdout.buffer.flush()'])
        try:
            with self.assertRaisesRegex(ValueError, 'request limit'):
                worker.request(b'x' * 1048576)
        finally:
            worker.close()
