from __future__ import annotations

import json
import shutil
from pathlib import Path


def generate(repo_path: str, out_dir: str, name: str, report: dict) -> dict:
    root = Path(repo_path)
    out = Path(out_dir) / name
    out.mkdir(parents=True, exist_ok=True)

    # plugin.json (portable, skills only)
    plugin_json = {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        "name": name,
        "version": "1.0.0",
        "description": f"Skill bundle generated from {report.get('repo_url', '')}",
        "author": {"name": "BlueStarJL"},
        "license": "MIT",
        "homepage": report.get("repo_url", ""),
        "keywords": ["skills", "generated"],
    }
    (out / "plugin.json").write_text(json.dumps(plugin_json, indent=2), encoding="utf-8")

    # copy skills
    skills_out = out / "skills"
    skills_out.mkdir(exist_ok=True)
    src_skills = root / "skills"
    if src_skills.is_dir():
        for child in src_skills.iterdir():
            if child.is_dir() and (child / "SKILL.md").is_file():
                dest = skills_out / child.name
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(child, dest)
                # adapt frontmatter name to namespaced
                skill_md = dest / "SKILL.md"
                text = skill_md.read_text(encoding="utf-8")
                # replace name: <child.name> with name: <child.name> (keep dir name)
                skill_md.write_text(text, encoding="utf-8")
    else:
        # create a skill from README
        d = skills_out / name
        d.mkdir(exist_ok=True)
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Generated skill bundle from {report.get('repo_url', '')}.\n---\n\nSee source repo.\n",
            encoding="utf-8",
        )

    # copy bracket.py etc? Copy all top-level .py and md? Copy supporting files into first skill dir
    first_skill = None
    if skills_out.exists():
        for c in skills_out.iterdir():
            if c.is_dir():
                first_skill = c
                break
    if first_skill:
        for f in root.iterdir():
            if f.is_file() and f.name not in ("README.md",):
                try:
                    shutil.copy2(f, first_skill / f.name)
                except Exception:
                    pass

    (out / "README.md").write_text(
        f"# {name}\n\nSkill bundle generated from {report.get('repo_url', '')}.\n\n"
        "Portable Agent Plugins v1 package (skills/ only).\n",
        encoding="utf-8",
    )
    return {"output_path": str(out), "files": sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file())}
