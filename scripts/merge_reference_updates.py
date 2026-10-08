"""Merge independently frozen reference text/metadata into canonical inputs.

Provider outputs are never read. Later run arguments take precedence for a
case, which lets a verified short replacement supersede its original batch.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.web_fetch.body_coverage import body_requirements
from harness.web_fetch.reference_inputs import require_mutable_inputs
from harness.web_fetch.reference_lifecycle import requires_fresh_reference
from harness.web_fetch.reference_quality import clean_reference_text


def merge(canonical_path: Path, runs: list[Path], skip: set[str], only_changed=False) -> list[str]:
    require_mutable_inputs(canonical_path)
    canonical = json.loads(canonical_path.read_text())
    indexed = {case["id"]: case for case in canonical["cases"]}
    updated = []
    for root in runs:
        frozen = json.loads((root / "dataset.json").read_text())
        for incoming in frozen["cases"]:
            case_id = incoming["id"]
            if case_id in skip or not requires_fresh_reference(incoming):
                continue
            if case_id not in indexed:
                raise ValueError("Unknown canonical case: " + case_id)
            current = indexed[case_id]
            if (only_changed and current.get("ground_truth_main_content") ==
                    incoming.get("ground_truth_main_content")):
                continue
            for key in ("url", "track"):
                if current.get(key) != incoming.get(key):
                    raise ValueError(f"{case_id}: {key} changed")
            raw_reference = incoming["ground_truth_main_content"]
            reference = clean_reference_text(raw_reference)
            verification = dict(incoming["source_verification"])
            if reference != raw_reference:
                verification["extracted_reference_sha256"] = hashlib.sha256(raw_reference.encode()).hexdigest()
                verification["reference_normalization"] = (
                    "Remove U+200B, U+FEFF and discretionary U+00AD; raw capture unchanged.")
            verification["reference_sha256"] = hashlib.sha256(reference.encode()).hexdigest()
            current["ground_truth_main_content"] = reference
            current["source_verification"] = verification
            if current.get('body_requirements') is not None:
                current['body_requirements'] = body_requirements(reference)
            source = root / "sources" / case_id
            if source.is_dir():
                target = canonical_path.parent / "sources" / case_id
                target.mkdir(parents=True, exist_ok=True)
                shutil.copytree(source, target, dirs_exist_ok=True)
                (target/'reference.txt').write_text(reference)
            if case_id not in updated:
                updated.append(case_id)
    canonical_path.write_text(json.dumps(canonical, ensure_ascii=False, indent=2) + "\n")
    return updated


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canonical", type=Path, required=True)
    parser.add_argument("--runs", type=Path, nargs="+", required=True)
    parser.add_argument("--skip", default="")
    parser.add_argument("--only-changed", action="store_true",
        help="Keep the existing capture binding when independently observed text is unchanged")
    args = parser.parse_args()
    rows = merge(args.canonical, args.runs, set(filter(None, args.skip.split(","))), args.only_changed)
    print(json.dumps({"updated": len(rows), "case_ids": rows}, ensure_ascii=False, indent=2))
