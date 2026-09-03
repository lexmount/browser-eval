import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.web_fetch.config import expand_env, load_env_file, load_runtime_env


class ConfigTest(unittest.TestCase):
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
        self.assertEqual(build_parser().parse_args(['validate']).dataset,root/'evaluation/dataset.json')
        provider=SubprocessJsonlProvider('test',{'command':['python3'],'repo_path':'.'})
        self.assertEqual(provider._resolve_token('{harness_root}/scripts/oss/langchain_webbase_worker.py'),str(root/'scripts/oss/langchain_webbase_worker.py'))


if __name__ == "__main__":
    unittest.main()
