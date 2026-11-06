import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from generators import mcp_safe

from host_test_config import host_test_config

class ConsoleHostTests(unittest.TestCase):
    def setUp(self):
        self.host, self.python = host_test_config(windows_only=True)

    def test_indirect_rebinding_rejected_after_approved_import(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); repo = root/'repo'; pkg = repo/'src'/'rebound_fixture'; pkg.mkdir(parents=True)
            marker = root/'imported'; invoked = root/'invoked'
            (repo/'pyproject.toml').write_text('[project]\nname="fixture-rebound"\nversion="1"\ndependencies=["mcp>=1"]\n[project.scripts]\nfixture="rebound_fixture:main"\n')
            (pkg/'__init__.py').write_text('from .helper import run as main\n')
            (pkg/'helper.py').write_text('from pathlib import Path\nPath(' + repr(str(marker)) + ').touch()\ndef run(): pass\ndef replacement(): Path(' + repr(str(invoked)) + ').touch()\ndef replace(): globals().update(run=replacement)\nreplace()\n')
            out = Path(mcp_safe.generate(repo, root/'out', 'fixture', {'type':'mcp','_explicit_mcp':True})['output_path'])
            script = r'''
import os,sys
from pathlib import Path
from hermes_cli.agent_plugins import load_agent_plugin
from tools import mcp_tool_discovery,mcp_tool_lifecycle
from tools.terminal_tool import set_approval_callback
from model_tools import handle_function_call
out,root,marker=map(Path,sys.argv[1:])
p=load_agent_plugin(out,root/'data'); assert not p.diagnostics,p.diagnostics
name,config=next(iter(p.mcp_servers.items()))
try:
    tools=mcp_tool_discovery.register_mcp_servers({name:config}); assert len(tools)==1
    assert not marker.exists()
    os.environ['HERMES_INTERACTIVE']='1'; set_approval_callback(lambda *a,**k:'once')
    consent=root/'data'/'persistent-mcp-opt-in.txt'; consent.parent.mkdir(parents=True,exist_ok=True)
    consent.write_text('I TRUST THIS SOURCE WITH PERSISTENT FULL OS AUTHORITY',encoding='utf-8')
    result=handle_function_call(tools[0],{'toolName':'anything','arguments':{}})
    assert marker.exists(),result
    assert 'success' not in result.lower() or 'false' in result.lower(),result
    assert not (root/'invoked').exists(), 'rebound callable was invoked'
    print('REBINDING_REJECTED')
finally:
    set_approval_callback(None); mcp_tool_lifecycle.shutdown_mcp_servers()
'''
            env=dict(os.environ,HERMES_HOME=str(root/'home'),TMPDIR=tmp,TMP=tmp,TEMP=tmp,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH='')
            env['PATH']=str(self.python.parent)+os.pathsep+env.get('PATH','')
            for key in ('PYTHONHOME','HERMES_SINGLE_QUERY_SESSION','HERMES_GATEWAY_SESSION','HERMES_SESSION_PLATFORM','HERMES_CRON_SESSION','HERMES_YOLO_MODE'): env.pop(key,None)
            proc=subprocess.run([str(self.python),'-c',script,str(out),str(root),str(marker)],cwd=self.host,env=env,capture_output=True,text=True,timeout=100)
            self.assertEqual(proc.returncode,0,proc.stdout+proc.stderr)
            self.assertIn('REBINDING_REJECTED',proc.stdout)

    def test_console_missing_dependency_never_executes_source(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); repo = root/'repo'; pkg = repo/'src'/'missing_fixture'; pkg.mkdir(parents=True)
            marker = root/'marker'
            (repo/'pyproject.toml').write_text('[project]\nname="fixture"\nversion="1"\ndependencies=["mcp>=1", "g2h-impossible-distribution-xyz>=1"]\n[project.scripts]\nfixture="missing_fixture:main"\n')
            (pkg/'__init__.py').write_text('from pathlib import Path\nPath(' + repr(str(marker)) + ').touch()\ndef main(): pass\n')
            out = Path(mcp_safe.generate(repo, root/'out', 'missing', {'type':'mcp','_explicit_mcp':True})['output_path'])
            script = r'''
import os, sys
from pathlib import Path
from hermes_cli.agent_plugins import load_agent_plugin
from tools import mcp_tool_discovery, mcp_tool_lifecycle
from tools.terminal_tool import set_approval_callback
from model_tools import handle_function_call
out,root,marker=map(Path,sys.argv[1:])
p=load_agent_plugin(out,root/'data'); assert not p.diagnostics,p.diagnostics
name,config=next(iter(p.mcp_servers.items()))
try:
    tools=mcp_tool_discovery.register_mcp_servers({name:config}); assert len(tools)==1,tools
    assert not marker.exists()
    os.environ['HERMES_INTERACTIVE']='1'; set_approval_callback(lambda *a,**k:'once')
    consent=root/'data'/'persistent-mcp-opt-in.txt'; consent.parent.mkdir(parents=True,exist_ok=True)
    consent.write_text('I TRUST THIS SOURCE WITH PERSISTENT FULL OS AUTHORITY',encoding='utf-8')
    import importlib.metadata
    try: importlib.metadata.version('g2h-impossible-distribution-xyz')
    except importlib.metadata.PackageNotFoundError: pass
    else: raise AssertionError('fixture dependency unexpectedly installed')
    result=handle_function_call(tools[0],{'toolName':'anything','arguments':{}})
    assert not marker.exists(), result
    assert 'success' not in result.lower() or 'false' in result.lower(), result
    print('MISSING_DEPENDENCY_OK')
finally:
    set_approval_callback(None); mcp_tool_lifecycle.shutdown_mcp_servers()
'''
            env=dict(os.environ,HERMES_HOME=str(root/'home'),TMPDIR=tmp,TMP=tmp,TEMP=tmp,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH='')
            env['PATH']=str(self.python.parent)+os.pathsep+env.get('PATH','')
            for key in ('PYTHONHOME','HERMES_SINGLE_QUERY_SESSION','HERMES_GATEWAY_SESSION','HERMES_SESSION_PLATFORM','HERMES_CRON_SESSION','HERMES_YOLO_MODE'): env.pop(key,None)
            proc=subprocess.run([str(self.python),'-c',script,str(out),str(root),str(marker)],cwd=self.host,env=env,capture_output=True,text=True,timeout=100)
            self.assertEqual(proc.returncode,0,proc.stdout+proc.stderr)
            self.assertIn('MISSING_DEPENDENCY_OK',proc.stdout)
            self.assertFalse(marker.exists())

    def test_console_real_host_gate_state_and_tamper(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp); repo = root/'repo'; pkg = repo/'src'/'fixture_console'; pkg.mkdir(parents=True)
            marker = root/'marker'
            pidfile = root/'pid'
            (repo/'pyproject.toml').write_text('[project]\nname="fixture"\nversion="1"\ndependencies=["mcp>=1"]\n[project.scripts]\nmcp-fixture="fixture_console:main"\n')
            (pkg/'helper.py').write_text('value = 0\n')
            (pkg/'__init__.py').write_text('from .server import run_server as main\n')
            (pkg/'server.py').write_text('''from __future__ import annotations
import asyncio
import os
import sys
from pathlib import Path
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types
from . import helper
from importlib.metadata import version, metadata
assert version('FIXTURE') == '1'
assert metadata('fixture')['Name'] == 'fixture'
Path(%r).touch()
Path(%r).write_text(str(os.getpid()))
async def list_tools(ctx, params):
    return types.ListToolsResult(tools=[types.Tool(name='increment', description='count', inputSchema={'type':'object'})])
async def call_tool(ctx, params):
    helper.value += 1
    return types.CallToolResult(content=[types.TextContent(type='text', text=str(helper.value))])
async def run():
    s=Server('fixture', on_list_tools=list_tools, on_call_tool=call_tool)
    async with stdio_server() as (r,w): await s.run(r,w,s.create_initialization_options())
def run_server():
    assert sys.argv[1:] == ['--timezone', 'UTC', 'literal ; & $(no-shell)', '\\U0001f680']
    asyncio.run(run())
''' % (str(marker), str(pidfile)))
            import importlib.util, json
            spec = importlib.util.spec_from_file_location('g2h_converter_test', Path(__file__).resolve().parents[1] / 'tools.py')
            converter = importlib.util.module_from_spec(spec); spec.loader.exec_module(converter)
            (repo/'README.md').write_text('MCP server')
            report = converter.detector.detect_type(str(repo)); report['repo_path'] = str(repo)
            # Low-level MCP Server is intentionally outside automatic FastMCP recognition.
            self.assertEqual(report['type'], 'unknown', report)
            result = json.loads(converter._convert_detected(report, 'mcp', 'fixture', str(root/'out')))
            self.assertEqual(result.get('type'), 'mcp', result)
            out = Path(result['output_path'])
            self.assertFalse(marker.exists())
            script = r'''
import os, sys
from pathlib import Path
from hermes_cli.agent_plugins import load_agent_plugin
from tools import mcp_tool_discovery, mcp_tool_lifecycle
from tools.terminal_tool import set_approval_callback
from model_tools import handle_function_call
out, root, marker = map(Path, sys.argv[1:])
p=load_agent_plugin(out,root/'data')
assert not p.diagnostics, p.diagnostics
name,config=next(iter(p.mcp_servers.items()))
try:
    tools=mcp_tool_discovery.register_mcp_servers({name:config}); assert len(tools)==1,tools
    tool=tools[0]; args={'toolName':'increment','arguments':{}}
    assert not marker.exists()
    os.environ['HERMES_SINGLE_QUERY_SESSION']='1'
    assert 'not approve' in handle_function_call(tool,args).lower()
    assert not marker.exists()
    os.environ.pop('HERMES_SINGLE_QUERY_SESSION'); os.environ['HERMES_INTERACTIVE']='1'
    set_approval_callback(lambda *a,**k:'n')
    assert 'not approve' in handle_function_call(tool,args).lower()
    assert not marker.exists()
    set_approval_callback(lambda *a,**k:'once')
    consent=root/'data'/'persistent-mcp-opt-in.txt'; consent.parent.mkdir(parents=True,exist_ok=True)
    consent.write_text('I TRUST THIS SOURCE WITH PERSISTENT FULL OS AUTHORITY',encoding='utf-8')
    configfile=root/'data'/'mcp-startup-args.json'
    configfile.write_text('{invalid', encoding='utf-8')
    assert 'invalid startup arguments' in handle_function_call(tool,args).lower()
    assert not marker.exists()
    configfile.write_text('["\\ud800"]', encoding='utf-8')
    assert 'invalid startup arguments' in handle_function_call(tool,args).lower()
    assert not marker.exists()
    configfile.write_text('["--timezone", "UTC", "literal ; & $(no-shell)", "\\ud83d\\ude80"]',encoding='utf-8')
    one=handle_function_call(tool,args); two=handle_function_call(tool,args)
    assert marker.exists() and '1' in one and '2' in two,(one,two)
    import ctypes
    kernel=ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.restype=ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_uint32]
    kernel.CloseHandle.argtypes=[ctypes.c_void_p]
    old=kernel.OpenProcess(0x100000, False, int((root/'pid').read_text()))
    assert old and kernel.WaitForSingleObject(old,0)==258
    configfile.write_text('["\\udfff"]', encoding='utf-8')
    assert 'invalid startup arguments' in handle_function_call(tool,args).lower()
    assert kernel.WaitForSingleObject(old,0)==258, 'invalid Unicode stopped live worker'
    configfile.write_text('[ "--timezone", "UTC", "literal ; & $(no-shell)", "\\ud83d\\ude80" ]',encoding='utf-8')
    assert 'startup arguments changed' in handle_function_call(tool,args).lower()
    assert kernel.WaitForSingleObject(old,10000)==0, 'old child survived config change'
    kernel.CloseHandle(old)
    restarted=handle_function_call(tool,args)
    assert '1' in restarted, restarted
    process=kernel.OpenProcess(0x100000, False, int((root/'pid').read_text()))
    assert process and kernel.WaitForSingleObject(process,0)==258, 'source child not running'
    (out/'source'/'fixture_console'/'server.py').write_text('tampered')
    result=handle_function_call(tool,args)
    assert 'digest mismatch' in result.lower(),result
    assert kernel.WaitForSingleObject(process,10000)==0, 'source child survived tamper cleanup'
    kernel.CloseHandle(process)
    print('CONSOLE_HOST_OK')
finally:
    set_approval_callback(None); mcp_tool_lifecycle.shutdown_mcp_servers()
'''
            env=dict(os.environ, HERMES_HOME=str(root/'home'), TMPDIR=tmp,TMP=tmp,TEMP=tmp,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH='')
            env['PATH']=str(self.python.parent)+os.pathsep+env.get('PATH','')
            for key in ('PYTHONHOME','HERMES_SINGLE_QUERY_SESSION','HERMES_GATEWAY_SESSION','HERMES_SESSION_PLATFORM','HERMES_CRON_SESSION','HERMES_YOLO_MODE'): env.pop(key,None)
            proc=subprocess.run([str(self.python),'-c',script,str(out),str(root),str(marker)],cwd=self.host,env=env,capture_output=True,text=True,timeout=100)
            self.assertEqual(proc.returncode,0,proc.stdout+proc.stderr)
            self.assertIn('CONSOLE_HOST_OK',proc.stdout)
