import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from generators import skill_bundle


class SkillBundleSafetyTests(unittest.TestCase):
    def test_nested_env_rejected_before_output(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp)
            skill = root / 'repo' / 'skills' / 'example'
            (skill / 'nested').mkdir(parents=True)
            (skill / 'SKILL.md').write_text('normal instruction')
            (skill / 'nested' / '.env.local').write_text('SYNTHETIC_CANARY=not-a-secret')
            with self.assertRaises(ValueError):
                skill_bundle.generate(str(root / 'repo'), str(root / 'out'), 'demo', {})
            self.assertFalse((root / 'out' / 'demo').exists())

    def test_sensitive_names_and_reparse_are_rejected(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp)
            skill = root / 'repo' / 'skills' / 'example'
            (skill / 'nested').mkdir(parents=True)
            (skill / 'SKILL.md').write_text('normal')
            for name in ('.env', '.ENV.production', 'credentials.json', 'auth.json', 'private.key', '.git', '.ssh'):
                item = skill / 'nested' / name
                item.write_text('synthetic')
                with self.subTest(name=name), self.assertRaises(ValueError):
                    skill_bundle.generate(str(root / 'repo'), str(root / 'out'), 'demo', {})
                self.assertFalse((root / 'out' / 'demo').exists())
                item.unlink()
            item = skill / 'nested' / 'junction'
            item.mkdir()
            original = Path.lstat
            def marked(path):
                result = original(path)
                if path == item:
                    class Reparse:
                        st_mode = result.st_mode
                        st_file_attributes = 0x400
                    return Reparse()
                return result
            with patch.object(Path, 'lstat', marked), self.assertRaises(ValueError):
                skill_bundle.generate(str(root / 'repo'), str(root / 'out'), 'demo', {})
            self.assertFalse((root / 'out' / 'demo').exists())

    def test_nested_symlink_rejected_and_normal_files_included(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as tmp:
            root = Path(tmp)
            skill = root / 'repo' / 'skills' / 'example'
            (skill / 'nested').mkdir(parents=True)
            (skill / 'SKILL.md').write_text('normal')
            (skill / 'nested' / 'guide.txt').write_text('safe synthetic content')
            link = skill / 'nested' / 'link'
            try:
                link.symlink_to(skill / 'nested' / 'guide.txt')
            except OSError:
                link = None
            if link:
                with self.assertRaises(ValueError):
                    skill_bundle.generate(str(root / 'repo'), str(root / 'out'), 'demo', {})
                self.assertFalse((root / 'out' / 'demo').exists())
                link.unlink()
            result = skill_bundle.generate(str(root / 'repo'), str(root / 'out'), 'demo', {})
            self.assertIn('skills/example/nested/guide.txt', [p.replace('\\', '/') for p in result['files']])
