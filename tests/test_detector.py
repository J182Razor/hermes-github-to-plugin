import sys
from pathlib import Path

HERMES_AGENT = Path("C:/Users/Lawa4721/AppData/Local/hermes/hermes-agent")
if str(HERMES_AGENT) not in sys.path:
    sys.path.insert(0, str(HERMES_AGENT))

import tempfile, os
from detector import detect_type


def test_detector_skill_bundle(tmp_path):
    # create minimal skill collection
    (tmp_path / "skills" / "foo").mkdir(parents=True)
    (tmp_path / "skills" / "foo" / "SKILL.md").write_text("---\nname: foo\ndescription: x\n---\nhi", encoding="utf-8")
    rep = detect_type(str(tmp_path))
    assert rep["type"] == "skill_bundle"
