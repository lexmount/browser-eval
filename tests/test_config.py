import os
import tempfile
import unittest
from pathlib import Path

from browser_harness_eval.config import expand_env, load_env_file


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


if __name__ == "__main__":
    unittest.main()
