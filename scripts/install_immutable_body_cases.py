#!/usr/bin/env python3
"""Install a fully verified immutable-body replacement cohort into the dataset."""
from __future__ import annotations

from copy import deepcopy
import argparse
import json
from pathlib import Path
import re
import shutil


ROOT = Path(__file__).resolve().parents[1]
OLD_IDS = [
    "english-encyclopedia", "lm-vite-guide", "lm-pnpm-install",
    "live-011-navigation-menu", "live-018-navigation-menu",
    "live-020-navigation-menu", "live-023-guides", "live-031-agent",
]
NEW_IDS = [
    "stable-wikipedia-ai-revision", "stable-rfc-8259-json",
    "stable-rfc-6455-websocket", "stable-browser-use-readme",
    "stable-openclaw-readme", "stable-nodejs-readme",
    "stable-wcag21-recommendation", "stable-xml10-recommendation",
]


def immutable_url(case: dict) -> bool:
    source = case.get("source_benchmark", {}).get("immutable_source", {})
    kind, revision, url = source.get("kind"), str(source.get("revision", "")), case["url"]
    if kind == "git_commit":
        return bool(re.fullmatch(r"[0-9a-f]{40}", revision) and f"/blob/{revision}/" in url)
    if kind == "wikipedia_revision":
        return bool(re.fullmatch(r"\d+", revision) and f"oldid={revision}" in url)
    if kind == "rfc":
        return url == f"https://www.rfc-editor.org/rfc/rfc{revision}.html"
    if kind == "dated_w3c_recommendation":
        return bool(re.search(r"/TR/\d{4}/REC-.+-\d{8}/$", url))
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frozen", type=Path, required=True)
    args = parser.parse_args()
    frozen = args.frozen.resolve()
    canonical_path = ROOT / "evaluation/dataset.json"
    canonical = json.loads(canonical_path.read_text(encoding="utf-8"))
    incoming = json.loads((frozen / "dataset.json").read_text(encoding="utf-8"))
    incoming_by_id = {case["id"]: case for case in incoming["cases"]}
    if set(incoming_by_id) != set(NEW_IDS):
        raise ValueError("Frozen replacement inventory is not exact")
    for case_id in NEW_IDS:
        case = incoming_by_id[case_id]
        verification = case.get("source_verification", {})
        if (verification.get("state") != "valid" or not case.get("ground_truth_main_content")
                or case["ground_truth_main_content"].startswith("PENDING")
                or not case.get("body_requirements") or not immutable_url(case)):
            raise ValueError(f"{case_id}: replacement is not a verified immutable body case")
    positions = {case["id"]: index for index, case in enumerate(canonical["cases"])}
    if set(positions) & set(OLD_IDS) != set(OLD_IDS) or set(positions) & set(NEW_IDS):
        raise ValueError("Canonical replacement inventory changed")
    for old_id, new_id in zip(OLD_IDS, NEW_IDS):
        canonical["cases"][positions[old_id]] = deepcopy(incoming_by_id[new_id])
        source = frozen / "sources" / new_id
        target = ROOT / "evaluation/sources" / new_id
        if target.exists():
            raise ValueError("Source target already exists: " + new_id)
        shutil.copytree(source, target)
    canonical.setdefault("comparison", {})["immutable_body_case_policy"] = {
        "required_for_new_full_text_cases": True,
        "allowed_sources": ["rfc", "dated_w3c_recommendation", "wikipedia_revision", "git_commit"],
        "volatile_pages": "structure_or_stable-contract tasks only",
    }
    canonical["comparison"]["case_replacements"] = dict(zip(OLD_IDS, NEW_IDS))
    canonical_path.write_text(json.dumps(canonical, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
