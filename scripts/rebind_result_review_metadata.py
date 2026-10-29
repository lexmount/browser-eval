"""Rebind neutral dataset metadata through the shared review and revision contracts."""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from harness.web_fetch.result_review import rebind_metadata_main


if __name__ == "__main__":
    rebind_metadata_main()
