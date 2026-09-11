import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from harness.web_fetch.artifact_auth import load_token, save_token


class ArtifactAuthTests(unittest.TestCase):
    def test_explicit_replacement_keeps_private_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'token'
            save_token('ahp_old_fixture',path)
            backup=save_token('ahp_new_fixture',path,replace_existing=True)
            self.assertEqual(backup.read_text().strip(),'ahp_old_fixture')
            self.assertEqual(path.read_text().strip(),'ahp_new_fixture')
            for file in (path,backup):
                self.assertEqual(stat.S_IMODE(file.stat().st_mode),0o600)

    def test_store_and_reuse_without_repository_env(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            path = Path(directory) / 'artifact-hub/token'
            save_token('ahp_test_fixture', path)
            self.assertEqual(load_token(path), 'ahp_test_fixture')
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            save_token('ahp_test_fixture', path)
            with self.assertRaises(ValueError):
                save_token('ahp_other_fixture', path)
            self.assertEqual(load_token(path), 'ahp_test_fixture')

    def test_explicit_override(self):
        with patch.dict(os.environ, {'ARTIFACT_HUB_TOKEN': 'ahp_override_fixture'}):
            self.assertEqual(load_token(Path('/does-not-exist')), 'ahp_override_fixture')

    def test_per_host_token_precedes_legacy_token(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {'HOME': directory}, clear=True
        ):
            config = Path(directory) / '.config/artifact-hub'
            save_token('ahp_legacy_fixture', config / 'token')
            save_token(
                'ahp_host_fixture',
                base_url='https://artifact-hub.app.lexmount.com',
            )
            self.assertEqual(
                load_token(base_url='https://artifact-hub.app.lexmount.com'),
                'ahp_host_fixture',
            )
            self.assertEqual(load_token(), 'ahp_legacy_fixture')

    def test_invalid_and_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'token'
            with self.assertRaises(ValueError):
                save_token('ahp_bad\nvalue', path)
            self.assertFalse(path.exists())
            path.symlink_to(Path(directory) / 'other')
            with self.assertRaises(ValueError):
                save_token('ahp_test_fixture', path)
            path.unlink()
            path.mkdir()
            with self.assertRaises(ValueError):
                save_token('ahp_test_fixture', path)


if __name__ == '__main__':
    unittest.main()
