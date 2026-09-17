"""Build the current measured report or its public-benchmark research companion."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from harness.web_fetch.report_build import build_report, build_public_benchmark_research, build_wcxb_report
from harness.web_fetch.storage import (
    current_evidence_dir,
    require_canonical_inputs,
    require_current_report_profile,
    require_evidence,
)

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence',type=Path)
    parser.add_argument('--public-benchmark-research',type=Path)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--wcxb-source',type=Path)
    parser.add_argument('--wcxb-sample',type=Path)
    args=parser.parse_args()
    if args.public_benchmark_research:
        if args.evidence:
            parser.error('--evidence and --public-benchmark-research are mutually exclusive')
        if args.output is None and json.loads((ROOT/'evaluation/current-report.json').read_text()).get('comparison_kind')=='webmainbench_frozen_html_to_markdown':
            parser.error('The independent public-benchmark report now contains reviewed measurements; a research-only draft requires an explicit separate --output')
        output=args.output or ROOT/'reports/webfetch-public-benchmarks'
        print(build_public_benchmark_research(args.public_benchmark_research,output))
        raise SystemExit(0)
    profile=json.loads((ROOT/'evaluation/current-report.json').read_text())
    if profile.get('comparison_kind')=='wcxb_frozen_html_to_markdown':
        if not args.evidence:
            parser.error('--evidence is required for the certified WCXB cohort')
        corpus=args.wcxb_source or Path.home()/'.browser-eval/public-benchmarks/wcxb'
        sample=args.wcxb_sample or ROOT/profile['sample_manifest']
        print(build_wcxb_report(args.evidence,corpus,sample,profile,args.output or ROOT/'reports/webfetch'))
        raise SystemExit(0)
    evidence=require_evidence(args.evidence or current_evidence_dir())
    if profile.get('comparison_kind')!='webmainbench_frozen_html_to_markdown':
        require_canonical_inputs(evidence,ROOT/'evaluation')
    require_current_report_profile(evidence,ROOT/'evaluation/current-report.json')
    print(build_report(evidence,args.output or ROOT/'reports/webfetch'))
