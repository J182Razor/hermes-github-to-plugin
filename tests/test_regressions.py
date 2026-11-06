"""Offline regressions for the installed plugin's real entrypoints."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import tomllib
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_plugin():
    # Match Hermes' path-based loader; do not add the plugin to sys.path.
    spec = importlib.util.spec_from_file_location("_g2h_test_plugin", ROOT / "__init__.py")
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    return plugin


class PluginRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get("HERMES_TEST_SCRATCH"))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.output = self.root / "output"
        self.plugin = load_plugin()
        self.tools = self.plugin.tools

    def report(self, kind):
        return {"type": kind, "repo_path": str(self.source),
                "repo_url": "https://example.invalid/repo", "suggested_name": "demo",
                "entrypoints": [], "env_vars": [], "endpoints": []}

    def convert(self, kind, name="demo"):
        with patch.object(self.tools.detector, "detect_repo", return_value=self.report(kind)), \
             patch.object(self.tools.subprocess, "run", return_value=types.SimpleNamespace(
                 returncode=0, stdout="ok", stderr="")) as run:
            result = json.loads(self.plugin.handle_convert_repo({
                "repo_url": "https://example.invalid/repo", "output_dir": str(self.output),
                "type": kind, "name": name}))
        return result, run

    def test_all_generators_run_through_actual_loader_without_path_or_module_collisions(self):
        (self.source / 'server.py').write_text('pass')
        (self.source / 'mcp.json').write_text(json.dumps({'mcpServers': {'demo': {'command': 'python', 'args': ['server.py']}}}))
        (self.source / 'openapi.json').write_text(json.dumps({'openapi': '3.0.0', 'paths': {'/x': {'get': {'operationId': 'x'}}}}))
        (self.source / 'pyproject.toml').write_text('[project]\nname="demo"\nversion="1"\n[project.scripts]\ndemo="demo:main"\n[tool.github-to-hermes]\npython-entrypoint="demo:main"\n')
        (self.source / 'demo.py').write_text('def main(): return 1')
        (self.source / 'skills' / 'demo').mkdir(parents=True)
        (self.source / 'skills' / 'demo' / 'SKILL.md').write_text('demo')
        expected = {

            "rest": {"plugin.json", "mcp.json", "server.py"},
            "cli": {"plugin.json", "mcp.json", "source.py", "adapter.py", "server.py"},
            "python_pkg": {"plugin.json", "mcp.json", "source.py", "adapter.py", "server.py"},
            "skill_bundle": {"plugin.json", "skills/demo/SKILL.md"},
        }
        # An unrelated installed top-level package must not hijack generator imports.
        with patch.dict("sys.modules", {"generators": types.ModuleType("generators")}):
            for kind, files in expected.items():
                with self.subTest(kind=kind):
                    self.output = self.root / kind
                    result, _ = self.convert(kind)
                    self.assertTrue(result["success"], result)
                    self.assertEqual(result["type"], kind)
                    self.assertTrue(files.issubset({p.replace("\\", "/") for p in result["files"]}), result)
                    for filename in files:
                        self.assertTrue((Path(result["output_path"]) / filename).is_file(), filename)

    def test_convert_does_not_install_dependencies(self):
        result, run = self.convert("unknown")
        self.assertFalse(result["success"], result)
        run.assert_not_called()

    def test_validate_is_static_and_does_not_run_source_or_subprocess(self):
        marker = self.root / 'executed'
        (self.source / '__init__.py').write_text(f'open({str(marker)!r}, "w").write("executed")')
        (self.source / 'plugin.json').write_text(json.dumps({
            '$schema': 'https://agent-plugins.org/schemas/1.0.0/plugin.schema.json', 'name': 'demo'}))
        self.assertFalse(marker.exists())
        with patch.object(self.tools.subprocess, 'run', side_effect=AssertionError('subprocess invoked')) as run:
            result = json.loads(self.plugin.handle_validate_plugin({'path': str(self.source)}))
        self.assertTrue(result['success'], result)
        self.assertTrue(result['static_only'])
        self.assertEqual(result['runtime_validation'], 'not run')
        self.assertTrue(result['warnings'])
        self.assertNotIn(str(self.source), json.dumps(result))
        self.assertFalse(marker.exists())
        run.assert_not_called()

    def test_validate_invalid_or_missing_path_is_clear_and_redacted(self):
        for args in ({}, {'path': str(self.source)}):
            with self.subTest(args=args), patch.object(self.tools.subprocess, 'run') as run:
                result = json.loads(self.plugin.handle_validate_plugin(args))
                self.assertFalse(result['success'])
                self.assertTrue(result['static_only'])
                self.assertEqual(result['runtime_validation'], 'not run')
                self.assertTrue(result.get('error') or result.get('errors'))
                self.assertTrue(result['warnings'])
                self.assertNotIn(str(self.source), json.dumps(result))
                run.assert_not_called()

    def test_registered_validation_loads_from_unrelated_cwd(self):
        (self.source / 'plugin.json').write_text(json.dumps({
            '$schema': 'https://agent-plugins.org/schemas/1.0.0/plugin.schema.json', 'name': 'demo'}))
        marker = self.root / 'executed'
        (self.source / '__init__.py').write_text(f'open({str(marker)!r}, "w").write("executed")')
        class Context:
            def __init__(self): self.handlers = {}
            def register_tool(self, name, handler, **kwargs): self.handlers[name] = handler
        ctx = Context()
        previous = os.getcwd()
        try:
            os.chdir(self.root)
            with patch.dict(os.environ, {'PYTHONPATH': ''}), \
                 patch.object(self.tools.subprocess, 'run', side_effect=AssertionError('subprocess invoked')) as run:
                plugin = load_plugin()
                plugin.register(ctx)
                self.assertFalse(marker.exists())
                result = json.loads(ctx.handlers['validate_plugin']({'path': str(self.source)}))
                self.assertTrue(result['success'], result)
                self.assertNotIn(str(self.source), json.dumps(result))
                self.assertFalse(marker.exists())
                run.assert_not_called()
        finally:
            os.chdir(previous)

    def test_schemas_describe_scaffolding_and_validation_without_automatic_install(self):
        validate = self.plugin.schemas.VALIDATE_PLUGIN_SCHEMA["description"]
        convert = self.plugin.schemas.CONVERT_REPO_SCHEMA["description"]
        self.assertNotIn("--install-deps", validate)
        self.assertIn("without running", convert.lower())

    def test_plugin_dependency_metadata_is_virtual_for_profile_coexistence(self):
        import yaml
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        manifest = yaml.safe_load((ROOT / "plugin.yaml").read_text(encoding="utf-8"))
        self.assertNotIn("build-system", project)
        self.assertIs(project["tool"]["uv"]["package"], False)
        expected = {"PyYAML>=6,<7", "pywin32>=306; sys_platform == 'win32'"}
        self.assertEqual(set(project["project"]["dependencies"]), expected)
        self.assertEqual(set(manifest["python_dependencies"]), expected)

    def test_reject_unsafe_supplied_names_before_detection_or_writes(self):
        for name in ("../escape", "..\\escape", "/absolute", "C:\\escape", ".", "..",
                     "CON", "bad\nname", "trailing."):
            with self.subTest(name=name), \
                 patch.object(self.tools.detector, "detect_repo", side_effect=AssertionError("unsafe name reached detection")) as detect, \
                 patch.object(self.tools.subprocess, "run") as run:
                result = json.loads(self.plugin.handle_convert_repo({
                    "repo_url": "https://example.invalid/repo", "output_dir": str(self.output), "name": name}))
                self.assertFalse(result["success"], result)
                self.assertIn("name", result["error"].lower())
                detect.assert_not_called()
                run.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_normal_supplied_name_is_supported(self):
        (self.source / 'skills' / 'demo').mkdir(parents=True)
        (self.source / 'skills' / 'demo' / 'SKILL.md').write_text('demo')
        result, _ = self.convert("skill_bundle", "Example_plugin-1.2")
        self.assertTrue(result["success"], result)
        self.assertEqual(Path(result["output_path"]), self.output / "Example_plugin-1.2")


if __name__ == "__main__":
    unittest.main()
