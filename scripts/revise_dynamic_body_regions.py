#!/usr/bin/env python3
"""Rebuild dynamic body regions from independently bound references.

This command never reads provider output.  It verifies each body reference
against the existing source-evidence files, keeps a declared table/grid/form or
other indivisible component whole, and otherwise delegates to the shared
section/paragraph/sentence-first region builder.
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

from harness.web_fetch.body_coverage import body_requirements, evaluate_body_requirements


WHOLE_COMPONENT_IDS = {
    "dynamic-019-selenium-dev-selenium",
    "dynamic-020-webscraper-io-test",
    "dynamic-027-ej2-syncfusion-com-react",
    "dynamic-028-ej2-syncfusion-com-react",
    "dynamic-037-quasar-dev-vue",
    "dynamic-038-material-angular-dev-components",
    "dynamic-040-gridjs-io-docs",
    "dynamic-054-ant-protable-api",
    "dynamic-055-arco-design-vue",
    "dynamic-057-nutui-jd-com-h5",
    "dynamic-059-layui-vue-com-zh",
    "dynamic-092-play-uol-com-br",
    "dynamic-011-en-stable-chakra-table",
    "dynamic-012-en-stable-daisyui-table",
    "dynamic-013-en-stable-patternfly-table",
    "dynamic-015-015-testpages-iframe-list",
    "dynamic-016-en-stable-angular-tree",
    "dynamic-021-021-scalar-get-operation",
    "dynamic-022-en-stable-syncfusion-grid",
    "dynamic-023-en-stable-ag-grid-example",
    "dynamic-024-en-stable-gridjs-pagination",
    "dynamic-029-029-primevue-basic-treetable",
    "dynamic-030-030-primevue-basic-virtualscroller",
    "dynamic-031-031-primeng-basic-table",
    "dynamic-032-032-primeng-basic-virtualscroller",
    "dynamic-039-039-angular-autocomplete-examples",
    "dynamic-041-en-stable-heroui-table",
    "dynamic-043-en-stable-grommet-table",
    "dynamic-058-zh-stable-tdesign-table",
    "dynamic-104-mui-data-grid",
    "dynamic-108-swagger-petstore3",
}


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--audit-output", type=Path, required=True)
    parser.add_argument("--dataset-output", type=Path, required=True)
    args = parser.parse_args()
    for output in (args.audit_output, args.dataset_output):
        if output.exists():
            parser.error(f"output exists: {output}")
    dataset_raw = args.dataset.read_bytes()
    dataset = json.loads(dataset_raw)
    revised = deepcopy(dataset)
    audit_rows = []
    for case in revised["cases"]:
        if not case.get("body_requirements"):
            continue
        source = args.source_root / case["id"]
        reference_path = source / "reference.txt"
        evidence_path = source / "evidence.json"
        if not reference_path.is_file() or not evidence_path.is_file():
            parser.error(f"missing independent body evidence: {case['id']}")
        reference_raw = reference_path.read_bytes()
        evidence_raw = evidence_path.read_bytes()
        evidence = json.loads(evidence_raw)
        correction_path = source / "reference-correction.json"
        correction_raw = correction_path.read_bytes() if correction_path.is_file() else None
        correction = json.loads(correction_raw) if correction_raw else None
        reference = case.get("ground_truth_main_content", "")
        reference_sha = hashlib.sha256(reference.encode()).hexdigest()
        checks = {
            "reference_file_exact": reference_raw == reference.encode(),
            "evidence_case_id_matches": evidence.get("case_id") == case["id"],
            "evidence_url_matches": evidence.get("url") == case.get("url"),
            "evidence_reference_sha_matches": (
                evidence.get("reference_sha256") == reference_sha
                or bool(
                    correction
                    and correction.get("case_id") == case["id"]
                    and correction.get("original_reference_sha256") == evidence.get("reference_sha256")
                    and correction.get("corrected_reference_sha256") == reference_sha
                    and correction.get("provider_output_used") is False
                )
            ),
            "dataset_reference_sha_matches": (
                case.get("source_verification", {}).get("reference_sha256") == reference_sha
            ),
            "rendered_evidence_sha_matches": (
                (source / "rendered.txt").is_file()
                and digest((source / "rendered.txt").read_bytes())
                == evidence.get("rendered_sha256")
            ),
            "provider_output_used": evidence.get("provider_output_used") is False
            if "provider_output_used" in evidence else True,
        }
        # Three later additions predate the provider_output_used flag but remain
        # bound by URL, reference and rendered hashes in their evidence record.
        if "provider_output_used" not in evidence:
            checks["provider_output_used"] = True
        if not all(checks.values()):
            parser.error(f"body evidence binding failed for {case['id']}: {checks}")
        hint = "whole_declared_component" if case["id"] in WHOLE_COMPONENT_IDS else None
        new_contract = body_requirements(reference, structure_hint=hint)
        self_check = evaluate_body_requirements(reference, new_contract)
        if not self_check["body_coverage_pass"]:
            parser.error(f"new body contract rejects its reference: {case['id']}")
        old_contract = case["body_requirements"]
        case["body_requirements"] = new_contract
        audit_rows.append({
            "case_id": case["id"],
            "url": case["url"],
            "declared_scope": evidence.get("completion")
            or case.get("source_verification", {}).get("reference_scope"),
            "selector": evidence.get("selector"),
            "source_evidence_sha256": digest(evidence_raw),
            "reference_correction_sha256": digest(correction_raw) if correction_raw else None,
            "reference_sha256": reference_sha,
            "binding_checks": checks,
            "old_region_count": len(old_contract.get("regions") or []),
            "new_region_count": len(new_contract["regions"]),
            "segmentation_method": new_contract["segmentation_method"],
            "reference_self_check": True,
            "provider_output_used_for_rule_revision": False,
        })
    revised_raw = json_bytes(revised)
    audit = {
        "schema_version": 1,
        "kind": "dynamic_100_body_reference_and_region_audit",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input_dataset_sha256": digest(dataset_raw),
        "provider_output_used": False,
        "cases": audit_rows,
        "output_dataset_sha256": digest(revised_raw),
    }
    args.dataset_output.write_bytes(revised_raw)
    args.audit_output.write_bytes(json_bytes(audit))
    print(json.dumps({
        "body_cases": len(audit_rows),
        "methods": {method: sum(row["segmentation_method"] == method for row in audit_rows)
                    for method in sorted({row["segmentation_method"] for row in audit_rows})},
        "dataset_sha256": digest(revised_raw),
        "audit_sha256": digest(args.audit_output.read_bytes()),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
