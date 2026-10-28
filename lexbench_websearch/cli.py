"""Offline benchmark inspection, export and deterministic scoring."""

import argparse
import json
from pathlib import Path
from .inputs import load_dataset, registry, digest
from .relevance import query_metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    commands.add_parser("version")
    release = commands.add_parser("release")
    release.add_argument("--dataset", type=Path, required=True)
    release.add_argument("--output", type=Path, required=True)
    release.add_argument("--version", required=True)
    release.add_argument("--scorer-revision", required=True)
    release.add_argument("--previous", type=Path)
    release.add_argument("--change-note")
    compare = commands.add_parser("compare-releases")
    compare.add_argument("left", type=Path)
    compare.add_argument("right", type=Path)

    def select_suite(value):
        data = registry()
        if value not in data["current"] and value not in data["datasets"]:
            raise argparse.ArgumentTypeError(
                "Choose a current suite: scenarios or factual"
            )
        return value

    for command in ("validate", "export"):
        sub = commands.add_parser(command)
        sub.add_argument(
            "dataset",
            type=select_suite,
            metavar="SUITE",
            help="Current suite: scenarios or factual.",
        )
    rebuild = commands.add_parser("rebuild")
    rebuild.add_argument("dataset", type=select_suite, metavar="factual")
    rebuild.add_argument("--csv", type=Path, required=True)
    score = commands.add_parser("score")
    score.add_argument(
        "input", type=Path, help="JSON with ranking, grades and optional cutoff"
    )
    args = parser.parse_args()
    selected = getattr(args, "dataset", None)
    identity = (registry()["current"].get(selected, selected)
                if args.command in {"validate", "export", "rebuild"} else None)
    if args.command == "version":
        from .versions import catalog_release

        catalog = catalog_release(require_current_scorer=False)
        data = registry()
        value = {
            "version": catalog["version"],
            "tag": catalog["tag"],
            "scoring_status": catalog["development"]["scoring_status"],
            "scoring_source_sha256": catalog["development"]["scoring_source_sha256"],
            "suites": {
                family: {"cases": data["datasets"][snapshot]["count"]}
                for family, snapshot in data["current"].items()
            },
        }
    elif args.command == "release":
        from .versions import freeze_release

        value = freeze_release(
            args.dataset,
            args.output,
            version=args.version,
            scorer_revision=args.scorer_revision,
            previous=json.loads(args.previous.read_bytes()) if args.previous else None,
            change_note=args.change_note,
        )
    elif args.command == "compare-releases":
        from .versions import compare_releases

        value = compare_releases(
            json.loads(args.left.read_bytes()), json.loads(args.right.read_bytes())
        )
    elif args.command == "list":
        data = registry()
        value = {
            "datasets": {
                family: {"cases": data["datasets"][snapshot]["count"]}
                for family, snapshot in data["current"].items()
            }
        }
    elif args.command == "rebuild":
        if identity != registry()["current"]["factual"]:
            parser.error("rebuild supports the factual suite")
        from .simpleqa import rebuild_dataset

        value = rebuild_dataset(args.csv.read_bytes())
        raw = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
        if digest(raw) != registry()["datasets"][identity]["sha256"]:
            raise ValueError("Rebuilt dataset does not match the registry hash")
        value = {"dataset": args.dataset, "sha256": digest(raw), "valid": True}
    elif args.command == "score":
        packet = json.loads(args.input.read_bytes())
        value = query_metrics(
            packet["ranking"], packet["grades"], packet.get("cutoff", 5)
        )
    else:
        value = load_dataset(identity)
        if args.command == "validate":
            value = {
                "dataset": args.dataset,
                "cases": len(value["cases"]),
                "valid": True,
            }
    print(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
