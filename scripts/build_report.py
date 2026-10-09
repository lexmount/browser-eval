"""Build the current measured report or its public-benchmark research companion."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from harness.web_fetch.report_build import build_report, build_public_benchmark_research, build_wcxb_report, build_webmainbench_full_report, build_diagnostic_cohorts, refresh_local_presentation
from harness.web_fetch.storage import (
    current_evidence_dir,
    require_canonical_inputs,
    require_current_report_profile,
    require_evidence,
)

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence',type=Path)
    parser.add_argument('--profile',type=Path,
                        help='current-report profile under evaluation/; requires an explicit output unless the profile declares report_output')
    parser.add_argument('--public-benchmark-research',type=Path)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--include-english',action='store_true',
                        help='also generate the English report; Chinese-only is the default')
    parser.add_argument('--refresh-presentation', type=Path,
                        help='refresh an existing local report using a hash-bound current-local-results.json; preserve measured evidence')
    parser.add_argument('--local-audit',action='store_true',
                        help='build an explicitly local, non-publishable audit report from bound evidence')
    parser.add_argument('--diagnostic-cohorts',action='store_true',
                        help='render an explicitly diagnostic cohort model with the maintained WebFetch template')
    parser.add_argument('--wcxb-source',type=Path)
    parser.add_argument('--wcxb-sample',type=Path)
    parser.add_argument('--wcxb',action='store_true',help='Build the independently bound WCXB report')
    parser.add_argument('--webmainbench-full',action='store_true',help='Build the separately certified complete 545-case report')
    args=parser.parse_args()
    from harness.reporting.templates import resolve_template
    resolve_template('webfetch')
    if args.refresh_presentation:
        if not args.output or args.evidence or args.profile or args.local_audit or args.include_english or args.diagnostic_cohorts or args.wcxb or args.webmainbench_full or args.public_benchmark_research:
            parser.error('--refresh-presentation requires only an existing --output and its result binding')
        print(refresh_local_presentation(args.output, args.refresh_presentation))
        raise SystemExit(0)
    if args.profile:
        args.profile=(ROOT/args.profile).resolve() if not args.profile.is_absolute() else args.profile.resolve()
        if not args.profile.is_relative_to((ROOT/'evaluation').resolve()):
            parser.error('--profile must stay under evaluation/')
        if json.loads(args.profile.read_text()).get('variant') == 'diagnostic-cohorts':
            args.diagnostic_cohorts=True
    if args.diagnostic_cohorts:
        if not args.output or (not args.evidence and not args.profile):
            parser.error('diagnostic build requires --output and evidence or a bound profile')
        if args.local_audit or args.wcxb or args.webmainbench_full or args.public_benchmark_research or args.include_english:
            parser.error('--diagnostic-cohorts requires its own diagnostic evidence model and Chinese output')
        print(build_diagnostic_cohorts(args.evidence,args.output,profile=args.profile))
        raise SystemExit(0)
    if args.local_audit:
        if not args.evidence or not args.output:
            parser.error('--local-audit requires both --evidence and --output')
        if args.profile or args.wcxb or args.webmainbench_full or args.public_benchmark_research:
            parser.error('--local-audit cannot be combined with a release profile, WCXB, WebMainBench, or public benchmark research')
        evidence=require_evidence(args.evidence)
        print(build_report(evidence,args.output,
                           include_english=args.include_english,
                           local_audit=True))
        raise SystemExit(0)
    if sum(bool(value) for value in (args.profile, args.wcxb, args.webmainbench_full)) > 1:
        parser.error('--profile, --wcxb and --webmainbench-full are mutually exclusive')
    if args.webmainbench_full:
        if not args.evidence or args.include_english:
            parser.error('--webmainbench-full requires --evidence and generates Chinese only')
        print(build_webmainbench_full_report(args.evidence,args.output or ROOT/'reports/webmainbench-full'))
        raise SystemExit(0)
    if args.public_benchmark_research:
        if args.evidence:
            parser.error('--evidence and --public-benchmark-research are mutually exclusive')
        if args.output is None and json.loads((ROOT/'evaluation/current-report.json').read_text()).get('comparison_kind')=='webmainbench_frozen_html_to_markdown':
            parser.error('The independent public-benchmark report now contains reviewed measurements; a research-only draft requires an explicit separate --output')
        output=args.output or ROOT/'reports/webfetch-public-benchmarks'
        print(build_public_benchmark_research(
            args.public_benchmark_research,output,include_english=args.include_english))
        raise SystemExit(0)
    if args.profile:
        profile_path=args.profile
    else:
        profile_path=ROOT/('evaluation/wcxb/current-report.json' if args.wcxb else 'evaluation/current-report.json')
    profile=json.loads(profile_path.read_text())
    if args.wcxb and profile.get('comparison_kind')!='wcxb_frozen_html_to_markdown':
        parser.error('The WCXB profile does not describe the WCXB matrix')
    if profile.get('comparison_kind')=='wcxb_frozen_html_to_markdown':
        if not args.evidence:
            parser.error('--evidence is required for the certified WCXB cohort')
        corpus=args.wcxb_source or Path.home()/'.browser-eval/public-benchmarks/wcxb'
        sample=args.wcxb_sample or ROOT/profile['sample_manifest']
        print(build_wcxb_report(args.evidence,corpus,sample,profile,args.output or ROOT/'reports/wcxb'))
        raise SystemExit(0)
    evidence=require_evidence(args.evidence or current_evidence_dir())
    if profile.get('comparison_kind')!='webmainbench_frozen_html_to_markdown':
        canonical=(profile_path.parent if (profile_path.parent/'dataset.json').is_file()
                   else ROOT/'evaluation')
        require_canonical_inputs(evidence,canonical)
    require_current_report_profile(evidence,profile_path)
    declared_output=profile.get('report_output')
    if args.profile and args.output is None and not declared_output:
        parser.error('a custom --profile requires --output or report_output in the profile')
    output=(args.output or (ROOT/declared_output if declared_output else ROOT/'reports/webfetch'))
    print(build_report(evidence,output,
                       include_english=args.include_english))
