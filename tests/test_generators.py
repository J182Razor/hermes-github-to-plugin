import sys
from pathlib import Path

HERMES_AGENT = Path("C:/Users/Lawa4721/AppData/Local/hermes/hermes-agent")
if str(HERMES_AGENT) not in sys.path:
    sys.path.insert(0, str(HERMES_AGENT))

import tempfile, os
from generators import skill_bundle


def test_generator_files(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "skills" / "foo").mkdir(parents=True)
    (src / "skills" / "foo" / "SKILL.md").write_text("---\nname: foo\ndescription: x\n---\nhi", encoding="utf-8")
    out = tmp_path / "out"
    res = skill_bundle.generate(str(src), str(out), "demo", {"repo_url": "https://example.com"})
    assert Path(res["output_path"]).exists()
    assert (Path(res["output_path"]) / "plugin.json").exists()
