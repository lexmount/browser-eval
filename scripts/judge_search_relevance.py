"""Run explicitly selected model relevance judgments on a frozen report."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness.web_search.relevance import judge_main

if __name__ == "__main__":
    judge_main()
