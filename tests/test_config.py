import os
import io
from contextlib import redirect_stdout
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.web_fetch.config import expand_env, load_env_file, load_runtime_env
from harness.web_fetch.accounts import HOSTED_KEYS, import_credentials, main_import_credentials


class ConfigTest(unittest.TestCase):
    def test_import_command_targets_declared_location_and_never_prints_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.env"
            secret = "synthetic-private-value"
            source.write_text("".join(f"{key}={secret}\n" for key in HOSTED_KEYS))
            for explicit in (False, True):
                target = root / ("explicit.env" if explicit else ".env")
                target.write_text(f"{HOSTED_KEYS[0]}=existing-synthetic-value\n")
                args = ["--source", str(source)]
                if explicit:
                    args += ["--target", str(target)]
                output = io.StringIO()
                with patch("harness.web_fetch.accounts.Path.cwd", return_value=root), redirect_stdout(output):
                    main_import_credentials(args)
                self.assertNotIn(secret, output.getvalue())
                self.assertNotIn("existing-synthetic-value", output.getvalue())
                self.assertIn(f"{HOSTED_KEYS[0]}=existing-synthetic-value", target.read_text())
                self.assertEqual(target.stat().st_mode & 0o777, 0o600)
                self.assertFalse(list(root.glob(".env-import-*")))
            self.assertIn(secret, source.read_text())

    def test_env_file_is_data_not_shell(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "must-not-exist"
            env_file = Path(directory) / ".env"
            env_file.write_text(
                f"SAFE_VALUE=ok\nINVALID=$(touch {marker})\n", encoding="utf-8"
            )
            old_safe = os.environ.pop("SAFE_VALUE", None)
            old_invalid = os.environ.pop("INVALID", None)
            try:
                load_env_file(env_file)
                self.assertEqual(os.environ["SAFE_VALUE"], "ok")
                self.assertEqual(os.environ["INVALID"], f"$(touch {marker})")
                self.assertFalse(marker.exists())
            finally:
                if old_safe is None:
                    os.environ.pop("SAFE_VALUE", None)
                else:
                    os.environ["SAFE_VALUE"] = old_safe
                if old_invalid is None:
                    os.environ.pop("INVALID", None)
                else:
                    os.environ["INVALID"] = old_invalid

    def test_expand_env_default(self) -> None:
        os.environ.pop("BHE_MISSING", None)
        self.assertEqual(expand_env("${BHE_MISSING:-fallback}"), "fallback")
        with patch.dict(os.environ, {"BHE_MISSING":""}):
            self.assertEqual(expand_env("${BHE_MISSING:-fallback}"), "fallback")

    def test_default_env_is_repo_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with patch('harness.web_fetch.config.ROOT', root), patch.dict(os.environ, {}, clear=True):
                load_runtime_env()  # Missing repository .env is fine for local methods.
                (root/'.env').write_text('BHE_LOCAL=repository\n')
                load_runtime_env()
                self.assertEqual(os.environ['BHE_LOCAL'],'repository')
                os.environ['BHE_LOCAL']='exported'
                load_runtime_env()
                self.assertEqual(os.environ['BHE_LOCAL'],'exported')
                with self.assertRaises(FileNotFoundError):load_runtime_env(root/'absent.env')

    def test_explicit_env_does_not_merge_default(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); (root/'.env').write_text('DEFAULT_ONLY=no\n')
            other=root/'chosen.env'; other.write_text('CHOSEN=yes\n')
            with patch('harness.web_fetch.config.ROOT',root),patch.dict(os.environ,{},clear=True):
                load_runtime_env(other)
                self.assertEqual(os.environ['CHOSEN'],'yes')
                self.assertNotIn('DEFAULT_ONLY',os.environ)

    def test_example_covers_provider_environment_variables(self):
        import re
        root=Path(__file__).resolve().parents[1]
        names=set(re.findall(r'^([A-Z_][A-Z0-9_]*)=',(root/'.env.example').read_text(),re.M))
        for path in (root/'configs').glob('providers*.json'):
            required=set(re.findall(r'\$\{([A-Z_][A-Z0-9_]*)',path.read_text()))
            self.assertFalse(required-names,(path,required-names))

    def test_migrated_roots_and_worker_paths(self):
        from harness.web_fetch.cli import build_parser
        from harness.web_fetch.providers.subprocess_jsonl import SubprocessJsonlProvider
        root=Path(__file__).resolve().parents[1]
        self.assertEqual(build_parser().parse_args(['validate']).dataset,root/'harness/web_fetch/data/fixture-dataset.json')
        provider=SubprocessJsonlProvider('test',{'command':['python3'],'repo_path':'.'})
        self.assertEqual(provider._resolve_token('{harness_root}/harness/web_fetch/workers/langchain_webbase_worker.py'),str(root/'harness/web_fetch/workers/langchain_webbase_worker.py'))

    def test_hosted_credentials_are_explicitly_and_selectively_imported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "home.env"
            target = root / "repo.env"
            source.write_text(
                "\n".join(
                    [f"export {key} = value-{index}" for index, key in enumerate(HOSTED_KEYS)]
                    + ["UNRELATED_SECRET=do-not-copy"]
                )
                + "\n",
                encoding="utf-8",
            )
            imported, missing = import_credentials(source, target)
            self.assertEqual(tuple(imported), HOSTED_KEYS)
            self.assertEqual(missing, [])
            payload = target.read_text(encoding="utf-8")
            self.assertNotIn("UNRELATED_SECRET", payload)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            with patch.dict(os.environ, {}, clear=True):
                load_env_file(target)
                self.assertTrue(all(os.environ.get(key) for key in HOSTED_KEYS))

    def test_import_preserves_existing_values_and_reports_missing_without_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "home.env"
            target = root / "repo.env"
            source.write_text(f"{HOSTED_KEYS[0]}=source\n", encoding="utf-8")
            target.write_text(f"{HOSTED_KEYS[0]}=existing\n", encoding="utf-8")
            imported, missing = import_credentials(source, target)
            self.assertEqual(imported, [])
            self.assertEqual(missing, list(HOSTED_KEYS[1:]))
            self.assertEqual(target.read_text(encoding="utf-8"), f"{HOSTED_KEYS[0]}=existing\n")


if __name__ == "__main__":
    unittest.main()

class CredentialAuthorityTests(unittest.TestCase):
    def test_repo_credentials_replace_parent_and_missing_keys_do_not_inherit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.env').write_text('EXA_API_KEY=repo-fixture-key\nPATH_CHOICE=repo-path\n')
            with patch('harness.web_fetch.config.ROOT', root), patch.dict(os.environ, {
                    'EXA_API_KEY': 'parent-fixture-key', 'TAVILY_API_KEY': 'parent-only-key',
                    'PATH_CHOICE': 'parent-path'}, clear=True):
                load_runtime_env()
                self.assertEqual(os.environ['EXA_API_KEY'], 'repo-fixture-key')
                self.assertNotIn('TAVILY_API_KEY', os.environ)
                self.assertEqual(os.environ['PATH_CHOICE'], 'parent-path')

    def test_explicit_external_env_cannot_supply_evaluation_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'other.env'
            source.write_text('EXA_API_KEY=external-fixture-key\n')
            with patch('harness.web_fetch.config.ROOT', root), patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(ValueError, 'repository .env'):
                    load_runtime_env(source)
                self.assertNotIn('EXA_API_KEY', os.environ)
