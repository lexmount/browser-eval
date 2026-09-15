#!/usr/bin/env python3
"""Replace mutable full-body gates with independently evidenced stable anchors."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.web_fetch.metrics import normalize_for_match  # noqa: E402


STABLE_FULL_BODY_CASES = {
    "static-simple": "stable_test_fixture",
    "ecommerce-list": "stable_test_fixture",
    "javascript-spa": "stable_test_fixture",
    "html-table": "stable_test_fixture",
    "redirect": "stable_test_fixture",
    "live-034-travel": "stable_test_fixture",
    "live-038-mystery": "stable_test_fixture",
    "live-071-science": "stable_test_fixture",
    "live-030-rfc-9110": "published_rfc",
}

STABLE_FACT_OVERRIDES = {
    "live-033-nasa-science": [
        ("page-purpose", ["NASA Science seeks to discover the secrets of the universe, search for life elsewhere, and protect and improve life on Earth and in space."]),
        ("science-strategy", ["Our science strategy articulates a forward-thinking vision and presents a comprehensive, cohesive and long-term roadmap for all NASA activities."]),
        ("science-careers", ["Discover career opportunities at NASA Science, where innovation, growth and collaboration shape the future of scientific exploration."]),
    ],
    "live-037-breadcrumb": [
        ("page-purpose", ["NOAA experts provide data, tools and information to help people understand, prepare for and adapt to our changing climate."]),
        ("climate-destinations", ["Drought.gov", "Heat.gov"]),
        ("focus-areas", ["Education Sanctuaries Charting Marine & Aviation Research Satellites Fisheries Ocean & Coasts Weather"]),
    ],
}


def _anchors(case: dict) -> list[dict]:
    override = STABLE_FACT_OVERRIDES.get(case["id"])
    if override is None:
        return deepcopy(case.get("required_facts", []))
    return [{"id": identifier, "alternatives": alternatives}
            for identifier, alternatives in override]


def stabilize(dataset: dict, sources_root: Path) -> tuple[dict, list[str]]:
    result = deepcopy(dataset)
    changed = []
    for case in result["cases"]:
        # Clean metadata emitted by an earlier draft of this migration.  Scoring
        # policy belongs to the scoring contract, not source capture metadata.
        benchmark = case.get("source_benchmark", {})
        prior_annotation = benchmark.get("stable_annotation", {})
        if prior_annotation.get("content_kind") == "changing_public_body":
            benchmark.pop("snapshot_metric_scope", None)
            benchmark.pop("stable_annotation", None)
        benchmark.pop("full_text_stability", None)
        case.pop("mutable_body_policy", None)
        if case.get("track", "web") != "web" or case.get("task_kind", "body") != "body":
            continue
        if benchmark.get("immutable_source"):
            continue
        if case["id"] in STABLE_FULL_BODY_CASES:
            continue
        if not case.get("ground_truth_main_content"):
            continue
        anchors = _anchors(case)
        if not anchors:
            raise ValueError(case["id"] + ": mutable body case lacks stable anchors")
        evidence = sources_root / case["id"] / "reference.txt"
        if not evidence.is_file():
            raise ValueError(case["id"] + ": stable-anchor evidence is missing")
        evidence_bytes = evidence.read_bytes()
        visible = normalize_for_match(evidence_bytes.decode("utf-8"))
        missing = [anchor["id"] for anchor in anchors
                   if not any(normalize_for_match(value) in visible
                              for value in anchor["alternatives"])]
        if missing:
            raise ValueError(case["id"] + ": stable anchors absent from evidence: " + ", ".join(missing))
        # A generic error phrase can be legitimate article prose (for example,
        # documentation discussing "Access denied" or RFC text mentioning a
        # 404 response).  The prior full-reference mode already exempted such
        # case-specific literals. Preserve that behavior explicitly before the
        # snapshot becomes diagnostic-only; blocked-page shape checks remain.
        case["forbidden_any"] = [value for value in case.get("forbidden_any", [])
                                 if normalize_for_match(value) not in visible]
        case["required_facts"] = deepcopy(anchors)
        case["acceptance_contract"] = {
            "version": 1,
            "schema": "body_text",
            "minimum_records": 0,
            "stable_text_anchors": deepcopy(anchors),
        }
        case.pop("body_requirements", None)
        case["mutable_body_policy"] = {
            "snapshot_metric_scope": "captured_snapshot_diagnostic",
            "stable_annotation": {
                "registry": "stable_annotations",
                "version": 2,
                "content_kind": "changing_public_body",
                "stable_fields": ["required_facts"],
                "structural_only_fields": ["mutable_full_text_snapshot"],
                "anchor_evidence": f"sources/{case['id']}/reference.txt",
                "anchor_evidence_sha256": hashlib.sha256(evidence_bytes).hexdigest(),
                "task_decision_scope": "explicit_stable_content",
                "word_overlap_scope": "captured_snapshot_diagnostic",
                "live_factual_correctness": None,
                "note": "Only independently evidenced stable text anchors affect acceptance; mutable full-page text remains diagnostic.",
            },
        }
        changed.append(case["id"])
    result.setdefault("comparison", {})["mutable_body_case_policy"] = {
        "schema": "body_text",
        "full_body_acceptance": "immutable_revisions_or_declared_stable_test_fixtures_only",
        "mutable_pages": "stable_text_anchors_only; full snapshots are diagnostic",
        "declared_stable_case_ids": sorted(STABLE_FULL_BODY_CASES),
    }
    return result, changed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "evaluation/dataset.json")
    parser.add_argument("--sources", type=Path, default=ROOT / "evaluation/sources")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output must be a new path")
    result, changed = stabilize(json.loads(args.dataset.read_text()), args.sources)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"changed_case_count": len(changed), "changed_case_ids": changed},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
