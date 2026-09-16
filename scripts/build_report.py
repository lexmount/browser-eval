"""Rebuild the current report from persistent measured evidence."""
import argparse,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from harness.web_fetch.report_build import build_report
from harness.web_fetch.storage import (
    current_evidence_dir,
    require_canonical_inputs,
    require_current_report_profile,
    require_evidence,
)

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence',type=Path,default=current_evidence_dir())
    parser.add_argument('--output',type=Path,default=ROOT/'reports/webfetch')
    parser.add_argument('--report-changes',type=Path)
    parser.add_argument('--omit-revision-receipts',action='store_true')
    args=parser.parse_args()
    evidence=require_evidence(args.evidence)
    require_canonical_inputs(evidence,ROOT/'evaluation')
    require_current_report_profile(evidence,ROOT/'evaluation/current-report.json')
    print(build_report(
        evidence,
        args.output,
        report_changes=args.report_changes,
        include_revision_receipts=not args.omit_revision_receipts,
    ))
