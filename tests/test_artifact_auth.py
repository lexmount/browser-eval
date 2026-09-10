import os
import io
from pathlib import Path
import runpy
import stat
import tempfile
import unittest
from unittest.mock import patch

from harness.web_fetch.artifact_auth import load_token, save_token, token_path


class ArtifactAuthTests(unittest.TestCase):
    def test_authorization_persists_for_target_without_loading_repository_env(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/authorize_artifact_hub.py'
        with patch.dict(os.environ, {'ARTIFACT_HUB_TOKEN': 'ahp_process_fixture'}, clear=True), \
                patch('sys.argv', [str(script), '--persist-existing', '--base-url', 'https://app.example']), \
                patch('harness.web_fetch.config.load_runtime_env', side_effect=AssertionError('must not read .env')), \
                patch('harness.web_fetch.artifact_auth.save_token', return_value=None) as save, \
                patch('sys.stdout', new_callable=io.StringIO) as output:
            runpy.run_path(str(script), run_name='__main__')
            save.assert_called_once_with('ahp_process_fixture', base_url='https://app.example', replace_existing=False)
            self.assertNotIn('ahp_process_fixture', output.getvalue())

    def test_per_host_precedence_and_independent_saves(self):
        with tempfile.TemporaryDirectory() as directory, patch('pathlib.Path.home', return_value=Path(directory)), patch.dict(os.environ, {}, clear=True):
            legacy = token_path()
            save_token('ahp_legacy_fixture', legacy)
            app = 'https://artifact-hub.app.example'
            dev = 'https://artifact-hub.dev.example'
            self.assertEqual(load_token(base_url=app), 'ahp_legacy_fixture')
            save_token('ahp_app_fixture', base_url=app)
            save_token('ahp_dev_fixture', base_url=dev)
            self.assertEqual(load_token(base_url=app), 'ahp_app_fixture')
            self.assertEqual(load_token(base_url=dev), 'ahp_dev_fixture')
            self.assertEqual(legacy.read_text().strip(), 'ahp_legacy_fixture')
            self.assertEqual(stat.S_IMODE(token_path(app).stat().st_mode), 0o600)
            with patch.dict(os.environ, {'ARTIFACT_HUB_TOKEN': 'ahp_environment_fixture'}):
                self.assertEqual(load_token(base_url=app), 'ahp_environment_fixture')
            token_path(app).write_text('')
            self.assertEqual(load_token(base_url=app), 'ahp_legacy_fixture')
            token_path(app).write_text('not-a-valid-token')
            with self.assertRaises(ValueError):
                load_token(base_url=app)

    def test_host_key_includes_port_and_cannot_escape_credential_directory(self):
        with tempfile.TemporaryDirectory() as directory, patch('pathlib.Path.home', return_value=Path(directory)):
            root = Path(directory) / '.config/artifact-hub/tokens'
            self.assertEqual(token_path('https://EXAMPLE.com:8443/path'), root / 'example.com:8443')
            for invalid in ['file:///tmp/token', 'https://..', 'https://.',
                            'https://user:password@example.com', 'https://example.com\\evil']:
                with self.subTest(base=invalid), self.assertRaises(ValueError):
                    token_path(invalid)

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
