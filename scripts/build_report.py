"""Rebuild the current report from persistent measured evidence."""
import argparse,json,sys
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
    parser.add_argument('--research-dataset',type=Path,
                        help='Canonical dataset for an explicit research evidence bundle')
    args=parser.parse_args()
    evidence=require_evidence(args.evidence)
    if args.research_dataset:
        canonical=args.research_dataset.resolve()
        expected=json.loads(canonical.read_text())
        actual=json.loads((evidence/'dataset.json').read_text())
        if actual != expected:
            raise ValueError('Persistent research evidence differs from its canonical dataset')
        for case in expected.get('cases',[]):
            for item in case.get('ground_truth_evidence',[]):
                relative=Path(item['source'])
                source=canonical.parent/relative
                frozen=evidence/relative
                if source.is_file() and (not frozen.is_file() or source.read_bytes()!=frozen.read_bytes()):
                    raise ValueError(f'Persistent research evidence differs from canonical source: {relative}')
    else:
        require_canonical_inputs(evidence,ROOT/'evaluation')
        require_current_report_profile(evidence,ROOT/'evaluation/current-report.json')
    print(build_report(evidence,args.output))
