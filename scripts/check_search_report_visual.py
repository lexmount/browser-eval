"""Capture maintained Web Search report reader evidence."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from harness.web_search.visual import main

if __name__ == '__main__':
    main()
