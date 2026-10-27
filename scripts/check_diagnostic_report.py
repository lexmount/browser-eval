"""Check a WebFetch diagnostic edition without declaring it certified."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from harness.web_fetch.publication import verify_published_tree
from harness.web_fetch.report_diagnostic import check_diagnostic_report

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',type=Path,nargs='?')
    parser.add_argument('--output',dest='named_output',type=Path)
    parser.add_argument('--profile',type=Path,help='Independently planned cohort/case/version scope')
    parser.add_argument('--deployed-archive',type=Path,help='Exported deployed tree to compare byte-for-byte')
    args=parser.parse_args()
    if bool(args.output) == bool(args.named_output):
        parser.error('provide one output directory, positional or --output')
    output=args.output or args.named_output
    result=check_diagnostic_report(output,profile=args.profile)
    if args.deployed_archive:
        result['verified_files']=verify_published_tree(output,args.deployed_archive)
    print(json.dumps(result))
