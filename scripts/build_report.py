"""Rebuild the current report without provider calls."""
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from harness.web_fetch.report_build import build_report

if __name__ == '__main__':
    print(build_report(ROOT/'evaluation', ROOT/'reports/webfetch'))
