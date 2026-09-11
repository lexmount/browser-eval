#!/usr/bin/env python3
"""Keep full China-homepage capture outside Git and write compact rule evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "research/china-homepage-rule-evidence-2026-09-11.json"
RULES = ROOT / "research/china-homepage-rules.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def is_matching_link(link: dict, contract: dict) -> bool:
    parts = urlsplit(link["url"])
    return bool(
        parts.hostname in contract.get("hosts", [])
        and re.fullmatch(contract.get("entry_path", r"(?!)"), parts.path.rstrip("/"))
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    data_home = Path(os.environ.get("BROWSER_EVAL_DATA_HOME", Path.home() / ".browser-eval"))
    parser.add_argument(
        "--input",
        type=Path,
        default=data_home / "evidence/current/china-homepage-rule-evidence-full-2026-09-11.json",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    raw = json.loads(args.input.read_text())
    rules = {item["domain"]: item for item in json.loads(RULES.read_text())["cases"]}
    compacted = []
    for observation in raw["observations"]:
        rule = rules[observation["domain"]]
        contract = rule["acceptance_contract"]
        links = observation.get("links", [])
        if contract["schema"] == "named_link":
            kept = [link for link in links if is_matching_link(link, contract)]
        else:
            kept = links[:25]
        compacted.append(
            {
                **observation,
                "links": kept,
                "saved_link_count": len(kept),
                "links_compacted_for_repository": len(kept) < len(links),
            }
        )

    relative_raw = "$BROWSER_EVAL_DATA_HOME/evidence/current/" + args.input.name
    result = {
        **raw,
        "purpose": "compact independent direct-page evidence for per-site acceptance-rule review",
        "raw_capture": {
            "location": relative_raw,
            "sha256": sha256(args.input),
            "git_tracked": False,
        },
        "capture_contract": {
            **raw["capture_contract"],
            "repository_compaction": (
                "Retain full visible text; retain every link matching a proposed "
                "named-link contract; retain at most 25 links for page-identity "
                "cases. Full immutable capture remains outside Git."
            ),
        },
        "observations": compacted,
    }
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        f"Compacted {len(compacted)} observations to {args.output} "
        f"({args.output.stat().st_size} bytes)"
    )


if __name__ == "__main__":
    main()
