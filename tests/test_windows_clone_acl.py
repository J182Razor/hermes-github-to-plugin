import os
import unittest
from pathlib import Path
from unittest.mock import patch

import detector


@unittest.skipUnless(os.name == 'nt', 'Windows ACLs')
class WindowsCloneACLTests(unittest.TestCase):
    def test_real_private_acl_and_cleanup(self):
        with patch.object(detector, '_run', return_value=(0, '', '')):
            path = detector.clone_repo('https://github.com/example/example')
        try:
            import pywintypes
            import win32security, win32api, ntsecuritycon
            sid = win32security.GetTokenInformation(win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY), win32security.TokenUser)[0]
            sd = win32security.GetNamedSecurityInfo(path, win32security.SE_FILE_OBJECT, win32security.OWNER_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION)
            self.assertEqual(sd.GetSecurityDescriptorOwner(), sid)
            self.assertTrue(sd.GetSecurityDescriptorControl()[0] & win32security.SE_DACL_PROTECTED)
            acl = sd.GetSecurityDescriptorDacl()
            self.assertEqual(acl.GetAceCount(), 1)
            ace = acl.GetAce(0)
            self.assertEqual(ace[0], (win32security.ACCESS_ALLOWED_ACE_TYPE, 3))
            self.assertEqual(ace[1], ntsecuritycon.FILE_ALL_ACCESS)
            self.assertEqual(ace[2], sid)
        finally:
            detector._cleanup_clone(path)
        self.assertFalse(Path(path).exists())

    def test_everyone_ace_is_rejected_and_cleaned(self):
        import pywintypes
        import win32security, ntsecuritycon
        real = win32security.GetNamedSecurityInfo
        created = []
        def broad(path, *args):
            created.append(path)
            sd = real(path, *args)
            acl = sd.GetSecurityDescriptorDacl()
            acl.AddAccessAllowedAceEx(win32security.ACL_REVISION, 3, ntsecuritycon.FILE_ALL_ACCESS, win32security.CreateWellKnownSid(win32security.WinWorldSid, None))
            sd.SetSecurityDescriptorDacl(True, acl, False)
            return sd
        with patch.object(win32security, 'GetNamedSecurityInfo', side_effect=broad), patch.object(detector, '_run') as run:
            with self.assertRaisesRegex(RuntimeError, 'ACL verification'):
                detector.clone_repo('https://github.com/example/example')
            run.assert_not_called()
        self.assertEqual(len(created), 1)
        self.assertFalse(Path(created[0]).exists())

    def test_missing_pywin32_fails_closed(self):
        import builtins
        real = builtins.__import__
        def missing(name, *args, **kwargs):
            if name == 'pywintypes':
                raise ImportError('simulated missing dependency')
            return real(name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=missing), patch.object(detector, '_run') as run:
            with self.assertRaisesRegex(RuntimeError, 'ACL support unavailable'):
                detector.clone_repo('https://github.com/example/example')
            run.assert_not_called()

    def test_collision_retries_without_touching_existing_directory(self):
        import secrets
        import tempfile
        parent = Path(tempfile.gettempdir())
        name = secrets.token_hex(16)
        existing = parent / ('g2h_' + name)
        existing.mkdir()
        marker = existing / 'marker'
        marker.write_text('untouched')
        try:
            with patch.object(detector.secrets, 'token_hex', side_effect=[name, secrets.token_hex(16)]), patch.object(detector, '_run', return_value=(0, '', '')):
                path = detector.clone_repo('https://github.com/example/example')
            try:
                self.assertNotEqual(Path(path), existing)
                self.assertEqual(marker.read_text(), 'untouched')
            finally:
                detector._cleanup_clone(path)
        finally:
            marker.unlink()
            existing.rmdir()
