"""Rebuild the current report from persistent measured evidence."""
import argparse,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from harness.web_fetch.report_build import build_report
from harness.web_fetch.storage import current_evidence_dir,require_canonical_inputs,require_evidence

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence',type=Path,default=current_evidence_dir())
    parser.add_argument('--output',type=Path,default=ROOT/'reports/webfetch')
    args=parser.parse_args()
    evidence=require_evidence(args.evidence)
    require_canonical_inputs(evidence,ROOT/'evaluation')
    print(build_report(evidence,args.output))
