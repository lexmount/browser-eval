"""Public entry point for the structured extraction track."""

import argparse
import json
from pathlib import Path

from harness.report_languages import add_language_argument
from harness.web_scraper.pipeline import (
    data_home,
    prepare,
    probe,
    reproduce,
    score_run,
    serve_fixtures,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=data_home())
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("prepare")
    sub.add_parser("reproduce")
    p = sub.add_parser("plan")
    p.add_argument("--methods", nargs="+", required=True)
    p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("serve")
    p.add_argument("--port", type=int, default=8767)
    p = sub.add_parser("repair-plan")
    p.add_argument("--content", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--options", type=Path, required=True)
    p.add_argument("--fixture-base", required=True)
    p.add_argument("--cases", type=Path)
    for command in ("probe", "run"):
        p = sub.add_parser(command)
        p.add_argument("--methods", nargs="+", required=True)
        p.add_argument("--fixture-base")
        p.add_argument("--fixture-suffix", choices=[".html", ""], default=".html")
        p.add_argument(
            "--run-dir",
            type=Path,
            help="Resume the exact frozen scope without repeating completed cases",
        )
        p.add_argument(
            "--min-call-interval",
            type=float,
            default=0,
            help="Minimum seconds between request starts; recorded before calls",
        )
        if command == "probe":
            p.add_argument("--largest-page", action="store_true")
    p = sub.add_parser("score")
    p.add_argument("run", type=Path)
    p.add_argument(
        "--reproject",
        action="store_true",
        help="Recompute native field projection without provider calls",
    )
    p.add_argument(
        "--output", type=Path, help="Separate score revision; never replaces collected evidence"
    )
    p = sub.add_parser("report")
    p.add_argument("--content", type=Path, required=True)
    p.add_argument("--profile", type=Path, required=True, help="External report-instance profile")
    p.add_argument("--output", type=Path, required=True)
    add_language_argument(p)
    p.add_argument("--run", type=Path)
    p = sub.add_parser("check")
    p.add_argument("output", type=Path)
    p.add_argument("--release", action="store_true")
    p.add_argument("--visual-receipt", type=Path)
    p = sub.add_parser("package")
    p.add_argument("report", type=Path)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--visual-receipt", type=Path, required=True)
    p = sub.add_parser("review")
    p.add_argument("--content", type=Path, required=True)
    p.add_argument("--work", type=Path, required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--limit", type=int)
    p.add_argument("--prepare-only", action="store_true")
    p.add_argument("--prior", type=Path, help="Completed audit whose unchanged outputs are reused")
    p = sub.add_parser("adjudicate")
    p.add_argument("--prior", type=Path, required=True)
    p.add_argument("--work", type=Path, required=True)
    p.add_argument("--rules", type=Path, required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--workers", type=int, default=4)
    p = sub.add_parser("audit-assemble")
    for flag in ("content", "initial", "supplemental", "adjudication", "output", "profile"):
        p.add_argument("--" + flag, type=Path, required=True)
    p.add_argument("--decisions", type=Path, help="Source-bound final reference decisions")
    args = parser.parse_args(argv)
    if args.command == "prepare":
        print("Verified", prepare(args.home)["cases"], "frozen pages")
    elif args.command == "reproduce":
        print(reproduce(args.home))
    elif args.command == "plan":
        from harness.web_scraper.certification import create_plan

        print(create_plan(args.home, args.methods, args.output))
    elif args.command == "serve":
        serve_fixtures(args.home, port=args.port)
    elif args.command == "repair-plan":
        from harness.web_scraper.repair import create_plan

        print(
            create_plan(
                args.home,
                args.content,
                args.output,
                json.loads(args.options.read_text()),
                args.fixture_base,
                json.loads(args.cases.read_text()) if args.cases else None,
            )
        )
    elif args.command in ("probe", "run"):
        print(
            probe(
                args.home,
                args.methods,
                fixture_base=args.fixture_base,
                fixture_suffix=args.fixture_suffix,
                full=args.command == "run",
                largest=getattr(args, "largest_page", False),
                min_call_interval=args.min_call_interval,
                run_dir=args.run_dir,
            )
        )
    elif args.command == "score":
        print(score_run(args.home, args.run, reproject=args.reproject, output=args.output))
    elif args.command == "report":
        from harness.web_scraper.report import build

        build(
            args.output,
            args.content,
            home=args.home,
            run=args.run,
            language=args.language,
            profile=args.profile,
        )
    elif args.command == "check":
        from harness.web_scraper.report import check

        print(check(args.output, release=args.release, visual_receipt=args.visual_receipt))
    elif args.command == "package":
        from harness.web_scraper.report import package

        print(package(args.report, args.output, visual_receipt=args.visual_receipt))
    elif args.command == "review":
        from harness.web_scraper.review import prepare as prepare_review
        from harness.web_scraper.review import run as run_review

        prepare_review(args.home, args.content, args.work, prior=args.prior)
        if not args.prepare_only:
            print(run_review(args.work, model=args.model, workers=args.workers, limit=args.limit))
    elif args.command == "adjudicate":
        from harness.web_scraper.review import prepare_adjudication
        from harness.web_scraper.review import run as run_review

        prepare_adjudication(args.prior, args.work, args.rules)
        print(run_review(args.work, model=args.model, workers=args.workers))
    elif args.command == "audit-assemble":
        from harness.web_scraper.review import assemble

        value = assemble(
            args.home,
            args.content,
            args.initial,
            args.supplemental,
            args.adjudication,
            args.output,
            profile=args.profile,
            decisions=args.decisions,
        )
        print({k: value[k] for k in ("case_count", "result_count", "unresolved")})


if __name__ == "__main__":
    main()
