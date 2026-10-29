"""Assemble exact headless measurements, never selecting retries by score."""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))
from harness.web_fetch.assembly import main

if __name__ == "__main__":
    main()
