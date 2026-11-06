import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

import tempfile, os
import unittest
from generators import skill_bundle


class GeneratorTests(unittest.TestCase):
    def test_generator_files(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as temp_dir:
            tmp_path = Path(temp_dir)
            src = tmp_path / "src"
            src.mkdir()
            (src / "skills" / "foo").mkdir(parents=True)
            (src / "skills" / "foo" / "SKILL.md").write_text("---\nname: foo\ndescription: x\n---\nhi", encoding="utf-8")
            out = tmp_path / "out"
            res = skill_bundle.generate(str(src), str(out), "demo", {"repo_url": "https://example.com"})
            self.assertTrue(Path(res["output_path"]).exists())
            self.assertTrue((Path(res["output_path"]) / "plugin.json").exists())
