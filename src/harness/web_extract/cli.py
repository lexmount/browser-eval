"""Public entry point for the structured extraction track."""

import argparse
import json
from pathlib import Path

from harness.report_languages import add_language_argument
from harness.web_extract.dataset import BENCHMARKS
from harness.web_extract.pipeline import (
    data_home,
    prepare,
    reproduce,
    score_run,
    serve_fixtures,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=data_home())
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--benchmark", choices=tuple(BENCHMARKS), default="zyte")
    p.add_argument("--source", type=Path)
    sub.add_parser("reproduce")
    for command in ("snapshots-build", "snapshots-verify"):
        p = sub.add_parser(command)
        p.add_argument("--dataset", action="append", required=True, metavar="NAME=HOME")
        p.add_argument("--output", type=Path, required=True)
        if command == "snapshots-verify":
            p.add_argument("--binding", type=Path, required=True)
        else:
            p.add_argument("--revision", help="HF commit or tag; default resolves latest once")
    p = sub.add_parser("snapshots-index")
    p.add_argument("--binding", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("host")
    p.add_argument("--provider", choices=("cloudflare", "external"), default="cloudflare")
    p.add_argument("--base", help="Public base URL for external hosting")
    p.add_argument("--revision", help="HF commit or tag; default resolves latest once")
    p.add_argument("--project", help="Override the registered Cloudflare Pages project")
    p.add_argument(
        "--approve-plan",
        type=Path,
        help="Explicitly approved deployment proposal; unchanged inputs required",
    )
    p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("snapshots-publish")
    p.add_argument("--binding", type=Path, required=True)
    p.add_argument(
        "--verification",
        type=Path,
        help="Verification receipt; defaults to the host binding receipt",
    )
    p.add_argument("--revision", required=True)
    p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("plan")
    p.add_argument("--methods", nargs="+", required=True)
    p.add_argument("--native-options", type=Path, help="Per-method frozen native options JSON")
    p.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="Maximum in-flight calls per method; freeze before collection",
    )
    p.add_argument(
        "--execution-purpose", choices=("quality", "latency", "throughput"), default="quality"
    )
    p.add_argument("--serial-reason", default="", help="Required when selecting one in-flight call")
    p.add_argument(
        "--execution-assessment",
        type=Path,
        action="append",
        dest="execution_assessments",
        default=[],
        help="Bound execution preflight assessment",
    )
    p.add_argument("--output", type=Path, required=True)
    p.add_argument(
        "--snapshots",
        type=Path,
        help="Verified public snapshot registry; required for URL-based methods",
    )
    p = sub.add_parser("preflight")
    p.add_argument("--profile", type=Path, required=True)
    p.add_argument("--method", required=True)
    p.add_argument("--run-dir", type=Path, required=True)
    p.add_argument("--assessment-output", type=Path, required=True)
    p.add_argument("--sample-size", type=int, default=8)
    p.add_argument("--wall-time-budget-seconds", type=float, default=7200)
    p = sub.add_parser("serve")
    p.add_argument("--port", type=int, default=8767)
    p = sub.add_parser("repair-plan")
    p.add_argument("--content", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--options", type=Path, required=True)
    p.add_argument("--fixture-base")
    p.add_argument("--cases", type=Path)
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
    p.add_argument(
        "--reference-revision", type=Path, help="Source-bound record reference correction"
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
    p = sub.add_parser("publish")
    for name in ("source", "target", "work", "visual-receipt"):
        p.add_argument("--" + name, type=Path, required=True)
    p = sub.add_parser("review")
    p.add_argument("--content", type=Path, required=True)
    p.add_argument("--work", type=Path, required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--limit", type=int)
    p.add_argument("--prepare-only", action="store_true")
    p.add_argument("--prior", type=Path, help="Completed audit whose unchanged outputs are reused")
    p = sub.add_parser("adjudicate")
    p.add_argument("--additional-prior", type=Path, action="append", default=[])
    p.add_argument("--prior", type=Path, required=True)
    p.add_argument("--work", type=Path, required=True)
    p.add_argument("--rules", type=Path, required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--workers", type=int, default=4)
    p = sub.add_parser("audit-assemble")
    for flag in ("content", "initial", "output", "profile"):
        p.add_argument("--" + flag, type=Path, required=True)
    for flag in ("supplemental", "adjudication", "reference-revision"):
        p.add_argument("--" + flag, type=Path)
    p.add_argument("--additional-review", type=Path, action="append", default=[])
    p.add_argument("--additional-adjudication", type=Path, action="append", default=[])
    p.add_argument("--decisions", type=Path, help="Source-bound final reference decisions")
    args = parser.parse_args(argv)
    if args.command == "host":
        from harness.web_extract.snapshots import host

        print(
            host(
                args.home,
                args.output,
                provider=args.provider,
                public_base=args.base,
                project=args.project,
                approve_plan=args.approve_plan,
                hf_revision=args.revision,
            )
        )
    elif args.command == "snapshots-publish":
        from harness.web_extract.snapshots import publish_receipt

        binding = json.loads(args.binding.read_text(encoding="utf-8"))
        verification = (
            json.loads(args.verification.read_text(encoding="utf-8"))
            if args.verification
            else binding["verification"]
        )
        publish_receipt(
            args.home, binding, verification, revision=args.revision, output=args.output
        )
    elif args.command == "snapshots-index":
        from harness.web_extract.snapshots import build_index

        build_index(json.loads(args.binding.read_text(encoding="utf-8")), args.output)
    elif args.command in ("snapshots-build", "snapshots-verify"):
        from harness.web_extract import snapshots

        homes = {}
        for item in args.dataset:
            name, home = item.split("=", 1)
            if name in homes:
                raise ValueError("Duplicate snapshot dataset")
            homes[name] = Path(home)
        if args.command == "snapshots-build":
            from huggingface_hub import HfApi

            from harness.web_extract.dataset import benchmark_config

            if len(homes) != 1:
                raise ValueError("Build one independent dataset snapshot at a time")
            home = next(iter(homes.values()))
            hf = benchmark_config(home)["snapshot_distribution"]
            revision = HfApi().dataset_info(hf["repository"], revision=args.revision).sha
            if snapshots.hf_inventory(hf, revision) != snapshots.inventory(home)[1]:
                raise ValueError("Prepared inputs differ from selected HF edition")
            result = snapshots.build(
                homes,
                args.output,
                huggingface={"repository": hf["repository"], "revision": revision},
            )
            print({name: len(item["cases"]) for name, item in result["datasets"].items()})
        else:
            result = snapshots.verify(homes, json.loads(args.binding.read_text(encoding="utf-8")))
            from harness.web_extract.pipeline import save_json

            save_json(args.output, result, exclusive=True)
            print({"verified_pages": result["verified_pages"]})
    elif args.command == "prepare":
        print(
            "Verified",
            prepare(args.home, benchmark=args.benchmark, source=args.source)["cases"],
            "frozen pages",
        )
    elif args.command == "reproduce":
        print(reproduce(args.home))
    elif args.command == "plan":
        from harness.web_extract.certification import create_plan

        print(
            create_plan(
                args.home,
                args.methods,
                args.output,
                snapshots=args.snapshots,
                concurrency=args.concurrency,
                execution_purpose=args.execution_purpose,
                serial_reason=args.serial_reason,
                require_execution_assessment=True,
                execution_assessments=args.execution_assessments,
                native_options=json.loads(args.native_options.read_text(encoding="utf-8"))
                if args.native_options
                else None,
            )
        )
    elif args.command == "preflight":
        from harness.web_extract.certification import execution_preflight

        print(
            execution_preflight(
                args.home,
                args.profile,
                args.method,
                args.run_dir,
                args.assessment_output,
                sample_size=args.sample_size,
                wall_time_budget_seconds=args.wall_time_budget_seconds,
            )
        )
    elif args.command == "serve":
        serve_fixtures(args.home, port=args.port)
    elif args.command == "repair-plan":
        from harness.web_extract.repair import create_plan

        print(
            create_plan(
                args.home,
                args.content,
                args.output,
                json.loads(args.options.read_text(encoding="utf-8")),
                args.fixture_base,
                json.loads(args.cases.read_text(encoding="utf-8")) if args.cases else None,
            )
        )
    elif args.command == "score":
        print(
            score_run(
                args.home,
                args.run,
                reproject=args.reproject,
                output=args.output,
                reference_revision=args.reference_revision,
            )
        )
    elif args.command == "report":
        from harness.web_extract.report import build

        build(
            args.output,
            args.content,
            home=args.home,
            run=args.run,
            language=args.language,
            profile=args.profile,
        )
    elif args.command == "check":
        from harness.web_extract.report import check

        print(check(args.output, release=args.release, visual_receipt=args.visual_receipt))
    elif args.command == "package":
        from harness.web_extract.report import package

        print(package(args.report, args.output, visual_receipt=args.visual_receipt))
    elif args.command == "publish":
        from harness.web_extract.publication import publish

        print(publish(args.source, args.target, args.work, args.visual_receipt))
    elif args.command == "review":
        from harness.web_extract.review import prepare as prepare_review
        from harness.web_extract.review import run as run_review
        from harness.web_extract.review import validate_review_options

        try:
            validate_review_options(limit=args.limit, workers=args.workers)
        except ValueError as error:
            parser.error(str(error))
        prepare_review(args.home, args.content, args.work, prior=args.prior)
        if not args.prepare_only:
            print(run_review(args.work, model=args.model, workers=args.workers, limit=args.limit))
    elif args.command == "adjudicate":
        from harness.web_extract.review import prepare_adjudication, validate_review_options
        from harness.web_extract.review import run as run_review

        try:
            validate_review_options(workers=args.workers)
        except ValueError as error:
            parser.error(str(error))
        prepare_adjudication(
            args.prior, args.work, args.rules, additional_priors=args.additional_prior
        )
        print(run_review(args.work, model=args.model, workers=args.workers))
    elif args.command == "audit-assemble":
        from harness.web_extract.review import assemble

        value = assemble(
            args.home,
            args.content,
            args.initial,
            args.supplemental,
            args.adjudication,
            args.output,
            profile=args.profile,
            decisions=args.decisions,
            additional_reviews=args.additional_review,
            additional_adjudications=args.additional_adjudication,
            reference_revision=args.reference_revision,
        )
        print({k: value[k] for k in ("case_count", "result_count", "unresolved")})


if __name__ == "__main__":
    main()
