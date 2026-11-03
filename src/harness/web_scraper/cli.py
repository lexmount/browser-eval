"""Public entry point for the structured extraction track."""

import argparse
from pathlib import Path

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
    p.add_argument("--language", choices=["en", "zh"], default="zh")
    p.add_argument("--run", type=Path)
    p = sub.add_parser("check")
    p.add_argument("output", type=Path)
    p.add_argument("--release", action="store_true")
    p.add_argument("--visual-receipt", type=Path)
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


if __name__ == "__main__":
    main()
