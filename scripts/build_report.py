"""Rebuild the current report without provider calls."""
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from harness.web_fetch.report_build import build_report

if __name__ == '__main__':
    source=ROOT/'evaluation/headless' if (ROOT/'evaluation/headless/dataset.json').exists() else ROOT/'evaluation'
    print(build_report(source, ROOT/'reports/webfetch'))
