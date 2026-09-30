"""Check a WebFetch diagnostic edition without declaring it certified."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from harness.web_fetch.report_diagnostic import check_diagnostic_report

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',type=Path,nargs='?')
    parser.add_argument('--output',dest='named_output',type=Path)
    args=parser.parse_args()
    if bool(args.output) == bool(args.named_output):
        parser.error('provide one output directory, positional or --output')
    print(json.dumps(check_diagnostic_report(args.output or args.named_output)))
