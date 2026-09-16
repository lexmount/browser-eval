#!/usr/bin/env python3
"""Project independently reviewed stable text into public per-case evidence.

This command never reads provider artifacts or results.  Each published anchor
must be supported either by all three completed source-page observations or by
the case's previously hash-bound independent reference text.  The projection
is deliberately separate from scoring: it makes the already-declared rule
auditable in the HTML report.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from harness.web_fetch.metrics import fact_in_text, normalize_for_match


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def canonical(value: str) -> str:
    return " ".join(str(value or "").split())


def supports(raw: bytes, value: str) -> bool:
    return fact_in_text(
        normalize_for_match(raw.decode("utf-8")),
        normalize_for_match(value),
    )


def render_text(case: dict, anchors: list[dict]) -> bytes:
    lines = [
        "Independent stable-text evidence",
        f"Case: {case['id']}",
        f"URL: {case['url']}",
        "Observation boundary: anonymous initial automatic load; no click, input, search, scroll, or pagination.",
        "Provider output used: no",
        "",
    ]
    for anchor in anchors:
        lines.append(f"[{anchor['id']}]")
        # Keep short punctuation- or price-like strings inside an explanatory
        # line.  The report's ordinary-text normalizer intentionally drops
        # some isolated symbol-heavy lines, while the same source text remains
        # valid when it appears in its original surrounding sentence.
        lines.extend("Observed ordinary text: " + value for value in anchor["alternatives"])
        lines.append("")
    return ("\n".join(lines).rstrip() + "\n").encode()


def project(dataset: dict, evidence_root: Path, observations: dict,
            adjudication: dict, baseline: dict) -> tuple[dict, list[dict]]:
    revised = deepcopy(dataset)
    baseline_cases = {row["id"]: row for row in baseline["cases"]}
    observed_cases = observations.get("cases") or {}
    audit_cases = {row["case_id"]: row for row in adjudication.get("cases", [])}
    projected = []
    unsupported = []
    for case in revised["cases"]:
        baseline_case = baseline_cases[case["id"]]
        if "source_benchmark" in baseline_case:
            case["source_benchmark"] = deepcopy(baseline_case["source_benchmark"])
        else:
            case.pop("source_benchmark", None)
        case.pop("stable_text_evidence", None)
        contract = case.get("acceptance_contract") or {}
        anchors = contract.get("stable_text_anchors") or []
        if not anchors:
            continue
        identifier = case["id"]
        observation = observed_cases.get(identifier) or {}
        audit = audit_cases.get(identifier) or {}
        audited_by_id = {row.get("id"): row for row in audit.get("anchors", [])}
        if any(audited_by_id.get(row.get("id")) != row for row in anchors):
            raise ValueError(f"{identifier}: dataset contains an anchor absent from adjudication")
        observation_complete = observation.get("all_observations_complete") is True
        successful_loads = [
            row for row in observation.get("observations", [])
            if row.get("status") == "success"
        ]

        annotation = (case.get("source_benchmark") or {}).get("stable_annotation") or {}
        prior_path = annotation.get("anchor_evidence")
        prior_raw = None
        prior_sha = None
        if prior_path:
            prior_file = evidence_root / prior_path
            if prior_file.is_file():
                prior_raw = prior_file.read_bytes()
                prior_sha = digest(prior_raw)
                declared = annotation.get("anchor_evidence_sha256")
                if declared and declared != prior_sha:
                    raise ValueError(f"{identifier}: prior stable-text evidence hash mismatch")

        manifest_anchors = []
        case_unsupported = []
        for anchor in anchors:
            sources = []
            for value in anchor.get("alternatives", []):
                source_observed = (
                    observation_complete
                    and len(successful_loads) >= 3
                    and all(
                        fact_in_text(
                            normalize_for_match("\n".join(load.get("ordinary_texts", []))),
                            normalize_for_match(value),
                        )
                        for load in successful_loads
                    )
                )
                source_prior = prior_raw is not None and supports(prior_raw, value)
                if not source_observed and not source_prior:
                    case_unsupported.append(f"{identifier}/{anchor.get('id')}")
                sources.append({
                    "alternative": value,
                    "three_source_loads": source_observed,
                    "prior_independent_reference": source_prior,
                })
            manifest_anchors.append({"id": anchor["id"], "support": sources})

        if case_unsupported:
            unsupported.extend(case_unsupported)
            continue

        folder = evidence_root / "sources" / identifier
        folder.mkdir(parents=True, exist_ok=True)
        text_path = folder / "stable-text-evidence.txt"
        manifest_path = folder / "stable-text-evidence.json"
        text_raw = render_text(case, anchors)
        manifest = {
            "schema_version": 1,
            "kind": "independent_stable_text_evidence_projection",
            "case_id": identifier,
            "url": case["url"],
            "source": "independent source-page observations and retained independent reference; no provider output",
            "provider_output_used": False,
            "interaction": {"clicks": 0, "inputs": 0, "searches": 0,
                            "scrolls": 0, "pagination_actions": 0},
            "three_load_observation_complete": observation_complete,
            "observation_sha256": [
                row.get("observation_sha256")
                for row in observation.get("observations", [])
                if row.get("observation_sha256")
            ],
            "prior_independent_evidence": ({"path": prior_path, "sha256": prior_sha}
                                           if prior_raw is not None else None),
            "anchors": manifest_anchors,
            "projected_text_sha256": digest(text_raw),
        }
        manifest_raw = json_bytes(manifest)
        text_path.write_bytes(text_raw)
        manifest_path.write_bytes(manifest_raw)

        case["stable_text_evidence"] = {
            "kind": "independent_source_observation_projection",
            "provider_output_used": False,
            "path": f"sources/{identifier}/stable-text-evidence.txt",
            "sha256": digest(text_raw),
            "manifest_path": f"sources/{identifier}/stable-text-evidence.json",
            "manifest_sha256": digest(manifest_raw),
        }
        projected.append({
            "case_id": identifier,
            "anchor_count": len(anchors),
            "evidence_sha256": digest(text_raw),
            "manifest_sha256": digest(manifest_raw),
        })
    if unsupported:
        raise ValueError(
            "anchors without independent source evidence: " + ", ".join(unsupported))
    return revised, projected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canonical", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--adjudication", type=Path, required=True)
    parser.add_argument("--baseline-dataset", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()

    canonical_raw = args.canonical.read_bytes()
    evidence_dataset_path = args.evidence_root / "dataset.json"
    evidence_raw = evidence_dataset_path.read_bytes()
    canonical = json.loads(canonical_raw)
    evidence_dataset = json.loads(evidence_raw)
    observations_raw = args.observations.read_bytes()
    adjudication_raw = args.adjudication.read_bytes()
    baseline_raw = args.baseline_dataset.read_bytes()
    baseline = json.loads(baseline_raw)
    projection_input = (canonical if canonical.get("cases") == evidence_dataset.get("cases")
                        else evidence_dataset)
    revised, projected = project(
        projection_input, args.evidence_root,
        json.loads(observations_raw), json.loads(adjudication_raw), baseline,
    )
    if canonical.get("cases") != evidence_dataset.get("cases") \
            and canonical.get("cases") != revised.get("cases"):
        parser.error("canonical and evidence cases differ beyond a resumable evidence projection")
    revised_raw = json_bytes(revised)
    args.canonical.write_bytes(revised_raw)
    evidence_dataset_path.write_bytes(revised_raw)
    receipt = {
        "schema_version": 1,
        "kind": "dynamic_100_stable_text_evidence_projection",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "provider_output_used": False,
        "provider_calls": 0,
        "input_canonical_dataset_sha256": digest(canonical_raw),
        "input_evidence_dataset_sha256": digest(evidence_raw),
        "source_observations_sha256": digest(observations_raw),
        "stable_text_adjudication_sha256": digest(adjudication_raw),
        "baseline_scope_dataset_sha256": digest(baseline_raw),
        "output_dataset_sha256": digest(revised_raw),
        "projected_case_count": len(projected),
        "projected_anchor_count": sum(row["anchor_count"] for row in projected),
        "cases": projected,
    }
    args.receipt.write_bytes(json_bytes(receipt))
    print(json.dumps({
        "output_dataset_sha256": digest(revised_raw),
        "projected_case_count": len(projected),
        "projected_anchor_count": receipt["projected_anchor_count"],
        "receipt_sha256": digest(args.receipt.read_bytes()),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
