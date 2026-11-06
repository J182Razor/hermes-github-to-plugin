import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

import tempfile, os
import unittest
from detector import detect_type


class DetectorTests(unittest.TestCase):
    def test_detector_skill_bundle(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as temp_dir:
            tmp_path = Path(temp_dir)
            (tmp_path / "skills" / "foo").mkdir(parents=True)
            (tmp_path / "skills" / "foo" / "SKILL.md").write_text("---\nname: foo\ndescription: x\n---\nhi", encoding="utf-8")
            rep = detect_type(str(tmp_path))
            self.assertEqual(rep["type"], "skill_bundle")
