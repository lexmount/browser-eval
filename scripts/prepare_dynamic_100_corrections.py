"""Prepare the confirmed dynamic-100 reference and rule corrections.

This is deterministic and provider-free.  It can update the tracked canonical
dataset, or assemble a temporary canonical package (dataset plus corrected
reference files) consumed by the audited revision commands.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

from harness.web_fetch.body_coverage import body_requirements


SCOPE_CASES = {
    "dynamic-020-webscraper-io-test",
    "dynamic-021-021-scalar-get-operation",
    "dynamic-024-en-stable-gridjs-pagination",
    "dynamic-033-033-vuetify-usage-table",
    "dynamic-107-redoc-cafe-api",
}


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def corrected_reference(identifier: str, text: str) -> str:
    if identifier == "dynamic-020-webscraper-io-test":
        return text.replace(" 1 2 3 4 < Previous Next >", "").strip()
    if identifier == "dynamic-021-021-scalar-get-operation":
        return (
            "v1.0.0 OpenAPI 3.1.2 Hello World Server: https://void.scalar.com "
            "GET / Request Example Shell Curl curl https://void.scalar.com/"
        )
    if identifier == "dynamic-024-en-stable-gridjs-pagination":
        return re.sub(r"Previous1Next$", "", text).strip()
    if identifier == "dynamic-033-033-vuetify-usage-table":
        start = text.index("#Usage")
        end = text.index("#API", start)
        return text[start:end].strip()
    if identifier == "dynamic-107-redoc-cafe-api":
        value = text
        for control in (
            "Download OpenAPI specification:Download", "Copy Expand all Collapse all",
            "CopyExpand allCollapse all", "Copy", "Expand all", "Collapse all",
        ):
            value = value.replace(control, " ")
        return re.sub(r"\s+", " ", value).strip()
    return text


def update_case(case: dict, reference: str | None) -> None:
    identifier = case["id"]
    if reference is not None:
        case["ground_truth_main_content"] = reference
        case["body_requirements"] = body_requirements(reference)
        verification = case.setdefault("source_verification", {})
        verification["reference_sha256"] = digest(reference)
    if identifier == "dynamic-020-webscraper-io-test":
        case["acceptance_contract"] = {
            "version": 1, "schema": "catalog_product",
            "minimum_records": 6, "maximum_records": 6,
            "stable_text_anchors": [
                {"id": "computers", "alternatives": ["Computers"]},
                {"id": "tablets", "alternatives": ["Tablets"]},
            ],
            "field_minimum_records": {"price": 6, "description": 6, "reviews": 6},
        }
    if identifier in {"dynamic-042-en-real-amazon-india", "dynamic-099-ar-real-amazon-ae-phone"}:
        contract = case["acceptance_contract"]
        contract["entry_path"] = r"/(?:[^/]+/)*(?:dp|gp/aw/d)/[A-Z0-9]{10}(?:/ref=[^/?#]+)?"
        contract["entry_identity_pattern"] = r"/(?:[^/]+/)*(?:dp|gp/aw/d)/([A-Z0-9]{10})(?:/|$)"
    if identifier == "dynamic-024-en-stable-gridjs-pagination":
        facts = case.setdefault("required_facts", [])
        if not any(fact.get("id") == "completion-status" for fact in facts):
            facts.append({
                "id": "completion-status",
                "alternatives": ["Showing 1 to 2 of 2 results"],
            })
    if identifier == "dynamic-025-025-tabulator-vertical-virtual-dom":
        forbidden = case.setdefault("forbidden_any", [])
        if "Loading Example..." not in forbidden:
            forbidden.append("Loading Example...")
    if identifier == "dynamic-044-en-real-github-trending":
        case["acceptance_contract"]["entry_path"] = (
            r"/(?!trending(?:/|$)|sponsors(?:/|$))"
            r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"
        )
    if identifier == "dynamic-107-redoc-cafe-api":
        case["forbidden_any"] = [
            value for value in case.get("forbidden_any", [])
            if value != "A new home for Redoc is on the way"
        ]


def add_regressions(path: Path) -> None:
    raw = path.read_text()
    document = json.loads(raw)
    existing = {row["id"] for row in document["cases"]}
    rows = [
        {
            "id": "amazon-tracking-suffix-remains-a-product-detail",
            "category": "acceptance-contract", "root_cause": "amazon_product_tracking_path_suffix",
            "failure_mode": "false-negative",
            "case": {"url": "https://www.amazon.example/search", "task_kind": "body",
                     "acceptance_contract": {"version": 1, "schema": "named_link", "minimum_records": 2,
                         "hosts": ["www.amazon.example"],
                         "entry_path": r"/(?:[^/]+/)*(?:dp|gp/aw/d)/[A-Z0-9]{10}(?:/ref=[^/?#]+)?",
                         "entry_identity_pattern": r"/(?:[^/]+/)*(?:dp|gp/aw/d)/([A-Z0-9]{10})(?:/|$)",
                         "stable_text_anchors": [{"id": "query", "alternatives": ["headphones"]}]}},
            "artifact": "headphones\n- [Alpha headset](/alpha/dp/B012345678/ref=sr_1_1)\n- [Beta headset](/gp/aw/d/B087654321/ref=sr_1_2)",
            "ok": True, "expected_task_pass": True,
            "expected_metrics": {"stable_structure_pass": True, "stable_unique_record_count": 2},
        },
        {
            "id": "amazon-non-ref-suffix-is-not-a-product-detail",
            "category": "acceptance-contract", "root_cause": "amazon_product_tracking_path_suffix",
            "failure_mode": "false-positive",
            "case": {"url": "https://www.amazon.example/search", "task_kind": "body",
                     "acceptance_contract": {"version": 1, "schema": "named_link", "minimum_records": 2,
                         "hosts": ["www.amazon.example"],
                         "entry_path": r"/(?:[^/]+/)*(?:dp|gp/aw/d)/[A-Z0-9]{10}(?:/ref=[^/?#]+)?",
                         "entry_identity_pattern": r"/(?:[^/]+/)*(?:dp|gp/aw/d)/([A-Z0-9]{10})(?:/|$)",
                         "stable_text_anchors": [{"id": "query", "alternatives": ["headphones"]}]}},
            "artifact": "headphones\n- [Alpha review](/alpha/dp/B012345678/reviews)\n- [Beta offers](/gp/aw/d/B087654321/offers)",
            "ok": True, "expected_task_pass": False,
            "expected_metrics": {"stable_structure_pass": False, "stable_unique_record_count": 0},
        },
        {
            "id": "dynamic-target-placeholder-cannot-pass",
            "category": "body-scope", "root_cause": "unrendered_dynamic_target_placeholder",
            "failure_mode": "false-positive",
            "case": {"url": "https://example.com/table", "task_kind": "body",
                     "required_facts": [{"id": "title", "alternatives": ["Virtual DOM"]}],
                     "forbidden_any": ["Loading Example..."]},
            "artifact": "Virtual DOM\nLoading Example...\nView Source",
            "ok": True, "expected_task_pass": False,
            "expected_metrics": {"forbidden_hits": ["Loading Example..."]},
        },
        {
            "id": "completed-dynamic-target-can-pass",
            "category": "body-scope", "root_cause": "unrendered_dynamic_target_placeholder",
            "failure_mode": "false-negative",
            "case": {"url": "https://example.com/table", "task_kind": "body",
                     "required_facts": [{"id": "title", "alternatives": ["Virtual DOM"]}],
                     "forbidden_any": ["Loading Example..."]},
            "artifact": "Virtual DOM\nName Age\nAlice 31\nBob 42",
            "ok": True, "expected_task_pass": True,
            "expected_metrics": {"forbidden_hits": []},
        },
        {
            "id": "github-route-links-are-not-repositories",
            "category": "acceptance-contract", "root_cause": "github_route_as_repository",
            "failure_mode": "false-positive",
            "case": {"url": "https://github.com/trending", "task_kind": "body",
                     "acceptance_contract": {"version": 1, "schema": "named_link",
                         "minimum_records": 2, "hosts": ["github.com"],
                         "entry_path": r"/(?!trending(?:/|$)|sponsors(?:/|$))[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",
                         "stable_text_anchors": [
                             {"id": "title", "alternatives": ["Trending"]},
                             {"id": "authors", "alternatives": ["Built by"]},
                         ]}},
            "artifact": "Trending\nBuilt by\n- [Python](https://github.com/trending/python)\n- [Explore](https://github.com/sponsors/explore)",
            "ok": True, "expected_task_pass": False,
            "expected_metrics": {"stable_structure_pass": False, "stable_unique_record_count": 0},
        },
        {
            "id": "github-owner-repository-links-are-records",
            "category": "acceptance-contract", "root_cause": "github_route_as_repository",
            "failure_mode": "false-negative",
            "case": {"url": "https://github.com/trending", "task_kind": "body",
                     "acceptance_contract": {"version": 1, "schema": "named_link",
                         "minimum_records": 2, "hosts": ["github.com"],
                         "entry_path": r"/(?!trending(?:/|$)|sponsors(?:/|$))[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",
                         "stable_text_anchors": [
                             {"id": "title", "alternatives": ["Trending"]},
                             {"id": "authors", "alternatives": ["Built by"]},
                         ]}},
            "artifact": "Trending\nBuilt by\n- [alpha / project](https://github.com/alpha/project)\n- [beta / tool](https://github.com/beta/tool)",
            "ok": True, "expected_task_pass": True,
            "expected_metrics": {"stable_structure_pass": True, "stable_unique_record_count": 2},
        },
    ]
    additions = [row for row in rows if row["id"] not in existing]
    if not additions:
        return
    marker = "\n  ]\n}\n"
    if not raw.endswith(marker):
        raise ValueError("Unexpected scoring regression formatting")
    rendered = []
    for row in additions:
        value = json.dumps(row, ensure_ascii=False, indent=2)
        rendered.append("\n".join("    " + line for line in value.splitlines()))
    path.write_text(raw[:-len(marker)] + ",\n" + ",\n".join(rendered) + marker)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--dataset-output", type=Path,
        help="Also write the deterministic canonical dataset to this explicit path.",
    )
    parser.add_argument("--regressions", type=Path)
    args = parser.parse_args()
    document = json.loads(args.dataset.read_text())
    args.output.mkdir(parents=True, exist_ok=True)
    source_out = args.output / "sources"
    for case in document["cases"]:
        reference = None
        if case["id"] in SCOPE_CASES:
            source = args.evidence / "sources" / case["id"]
            reference = corrected_reference(case["id"], (source / "reference.txt").read_text())
            target = source_out / case["id"]
            shutil.copytree(source, target, dirs_exist_ok=True)
            (target / "reference.txt").write_text(reference)
            (target / "reference-correction.json").write_text(json.dumps({
                "schema_version": 1, "case_id": case["id"],
                "original_reference_sha256": digest((source / "reference.txt").read_text()),
                "corrected_reference_sha256": digest(reference),
                "provider_output_used": False,
            }, ensure_ascii=False, indent=2) + "\n")
        update_case(case, reference)
    rendered = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    (args.output / "dataset.json").write_text(rendered)
    if args.dataset_output:
        args.dataset_output.write_text(rendered)
    if args.regressions:
        add_regressions(args.regressions)


if __name__ == "__main__":
    main()
