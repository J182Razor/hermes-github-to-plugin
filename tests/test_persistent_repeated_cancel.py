"""Exercise cancellation against the generated handler without external sinks."""
import asyncio
from pathlib import Path
import sys
import threading
import types
import unittest
from unittest import mock


class RepeatedCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_cancel_holds_lock_until_cleanup_then_releases(self):
        started = threading.Event()
        cleaning = threading.Event()
        release = threading.Event()
        calls = []

        class Manager:
            def handle(self, args, cancel):
                calls.append(args['label'])
                if args['label'] == 'first':
                    started.set()
                    if not cancel.wait(5):
                        raise RuntimeError('cancellation missing')
                    cleaning.set()
                    if not release.wait(5):
                        raise RuntimeError('cleanup release missing')
                return {'success': True}

        class Result:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        mcp = types.ModuleType('mcp')
        mcp.types = types.SimpleNamespace(CallToolResult=Result, TextContent=Result)
        server = types.ModuleType('mcp.server')
        server.Server = object
        stdio = types.ModuleType('mcp.server.stdio')
        stdio.stdio_server = object()
        adapter = types.ModuleType('adapter')
        adapter.SessionManager = Manager
        modules = {'mcp': mcp, 'mcp.server': server, 'mcp.server.stdio': stdio, 'adapter': adapter}
        namespace = {'__name__': 'generated_test'}
        with mock.patch.dict(sys.modules, modules):
            exec((Path(__file__).resolve().parents[1] / 'generators/deferred_server.py.txt').read_text(), namespace)

        def params(label):
            return types.SimpleNamespace(name='invoke_reviewed_server', arguments={'label': label})

        async def wait_event(event):
            self.assertTrue(await asyncio.to_thread(event.wait, 3))

        first = asyncio.create_task(namespace['call_tool'](None, params('first')))
        third = None
        try:
            await wait_event(started)
            first.cancel()
            await wait_event(cleaning)
            first.cancel()
            third = asyncio.create_task(namespace['call_tool'](None, params('third')))
            for _ in range(10):
                await asyncio.sleep(0)
            self.assertTrue(namespace['lock'].locked())
            self.assertEqual(calls, ['first'])
            self.assertFalse(first.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(first, 3)
            result = await asyncio.wait_for(third, 3)
            self.assertFalse(result.isError)
            self.assertEqual(calls, ['first', 'third'])
            self.assertFalse(namespace['lock'].locked())
        finally:
            release.set()
            await asyncio.gather(first, *( [third] if third else []), return_exceptions=True)
