"""Import only declared hosted-benchmark credentials into the repo .env."""

from pathlib import Path

from harness.web_fetch.accounts import main_import_credentials


if __name__ == "__main__":
    main_import_credentials(default_target=Path(__file__).resolve().parents[1] / ".env")
