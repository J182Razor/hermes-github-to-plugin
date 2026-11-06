from __future__ import annotations

import json
import os
import re
import shutil
import stat
from pathlib import Path


def _check_entry(path: Path) -> None:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise ValueError('links and reparse points in skill bundles are unsupported')
    if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
        raise ValueError('special files in skill bundles are unsupported')
    name = path.name.casefold()

    if (name == '.git' or name.startswith('.git.') or name in {'.env', '.envrc'} or name.startswith('.env.')
            or name in {'.ssh', '.aws', '.gnupg', '.netrc', '.npmrc', '.pypirc', '.docker',
                        'credentials', 'credential', 'auth', 'secrets', 'secret', 'id_rsa', 'id_ed25519'}
            or re.search(r'(^|[._-])(credentials?|auth|secrets?|tokens?|private[._-]?key|api[._-]?key)([._-]|$)', name)
            or name.startswith(('id_rsa.', 'id_ed25519.'))
            or name.endswith(('.pem', '.key', '.p12', '.pfx', '.keystore', '.jks'))):
        raise ValueError('sensitive path in skill bundle is unsupported')


def _check_tree(path: Path) -> None:
    _check_entry(path)
    if path.is_dir():
        with os.scandir(path) as entries:
            for entry in entries:
                _check_tree(Path(entry.path))


def generate(repo_path: str, out_dir: str, name: str, report: dict) -> dict:
    root = Path(repo_path)
    skills = root / 'skills'
    _check_entry(skills)
    if not skills.is_dir():
        raise ValueError('a real skills directory is required')
    entries = sorted(skills.iterdir())
    for entry in entries:
        _check_entry(entry)
    sources = [p for p in entries if p.is_dir() and (p / 'SKILL.md').is_file()]
    if not sources:
        raise ValueError('no self-contained skills/*/SKILL.md found')
    for src in sources:
        _check_tree(src)
    out = Path(out_dir) / name
    out.mkdir(parents=True, exist_ok=False)
    try:
        (out / 'plugin.json').write_text(json.dumps({'$schema': 'https://agent-plugins.org/schemas/1.0.0/plugin.schema.json', 'name': name, 'version': '1.0.0', 'description': 'Skill bundle; review required'}, indent=2), encoding='utf-8')
        for src in sources:
            shutil.copytree(src, out / 'skills' / src.name)
        (out / 'README.md').write_text('Review all content for embedded secrets, instructions and scripts before sharing or enabling. Filename checks cannot prove content is secret-free.\n', encoding='utf-8')
    except Exception:
        # Leave partial output for manual inspection; path-based deletion can race.
        raise
    return {'output_path': str(out), 'files': sorted(str(p.relative_to(out)) for p in out.rglob('*') if p.is_file())}
