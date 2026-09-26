"""Compose two certified six-method origin corrections into seven methods."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.web_fetch.safety import contained_path
from harness.web_fetch.webmainbench_correction import digest, read_rows, validate
from scripts.assemble_webmainbench_seven_methods import alias_row


BASE = ["moli", "lightpanda", "obscura", "chromium", "kitesurf"]
OLD = "moli-pr785-fixed"
NEW = "moli-content-fixed"
HISTORICAL = "moli-fixed"


def write_rows(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))


def copy_artifact(root: Path, output: Path, row: dict, key: str,
                  source_provider: str | None = None, provider: str | None = None) -> None:
    expected_sha256 = row.get("output_sha256")
    if not isinstance(expected_sha256, str) or not expected_sha256:
        raise ValueError(
            "Correction artifact is missing output_sha256: "
            + str(row.get("attempt_id", "unknown attempt"))
        )
    source = contained_path(root, row[key])
    if digest(source) != expected_sha256:
        raise ValueError("Correction artifact changed: " + row[key])
    relative = row[key]
    if source_provider and provider:
        relative = relative.replace(
            f"artifacts/{source_provider}/", f"artifacts/{provider}/", 1
        )
    target = contained_path(output, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def compose(seven: Path, prior_source: Path, prior_correction: Path,
            content_source: Path, content_correction: Path, output: Path) -> None:
    if output.exists():
        raise ValueError("Composite correction is immutable")
    seven_rows = read_rows(seven / "results.jsonl")
    prior_rows = read_rows(prior_source / "results.jsonl")
    content_rows = read_rows(content_source / "results.jsonl")
    prior_cert, old_replacements, old_calls = validate(
        prior_correction, prior_rows, digest(prior_source / "results.jsonl")
    )
    content_cert, new_replacements, new_calls = validate(
        content_correction, content_rows, digest(content_source / "results.jsonl")
    )
    if prior_cert["preflight"]["case_id"] != content_cert["preflight"]["case_id"]:
        raise ValueError("Correction case differs between repair cohorts")
    old_by = {row["provider"]: row for row in old_replacements}
    new_by = {row["provider"]: row for row in new_replacements}
    if any(old_by[provider] != new_by[provider] for provider in BASE):
        raise ValueError("Shared method corrections differ")
    rows = [copy.deepcopy(old_by[provider]) for provider in BASE]
    rows.append(alias_row(old_by[HISTORICAL], HISTORICAL, OLD))
    rows.append(alias_row(new_by[HISTORICAL], HISTORICAL, NEW))
    providers = BASE + [OLD, NEW]
    if {row["provider"] for row in rows} != set(providers):
        raise ValueError("Seven-method correction rows are incomplete")
    old_base_calls = [row for row in old_calls if row["provider"] in BASE]
    new_base_calls = [row for row in new_calls if row["provider"] in BASE]
    if old_base_calls != new_base_calls:
        raise ValueError("Shared method correction calls differ")
    calls = copy.deepcopy(old_base_calls)
    calls += [alias_row(row, HISTORICAL, OLD) for row in old_calls
              if row["provider"] == HISTORICAL]
    calls += [alias_row(row, HISTORICAL, NEW) for row in new_calls
              if row["provider"] == HISTORICAL]
    if len({row["attempt_id"] for row in calls}) != len(calls):
        raise ValueError("Seven-method correction call identities are not unique")
    output.mkdir(parents=True)
    for row in old_replacements:
        if row["provider"] in BASE:
            copy_artifact(prior_correction, output, row, "artifact_path")
    copy_artifact(prior_correction, output, old_by[HISTORICAL], "artifact_path", HISTORICAL, OLD)
    copy_artifact(content_correction, output, new_by[HISTORICAL], "artifact_path", HISTORICAL, NEW)
    for row in old_base_calls:
        copy_artifact(prior_correction, output, row, "physical_artifact_path")
    for row in old_calls:
        if row["provider"] == HISTORICAL:
            copy_artifact(prior_correction, output, row, "physical_artifact_path", HISTORICAL, OLD)
    for row in new_calls:
        if row["provider"] == HISTORICAL:
            copy_artifact(content_correction, output, row, "physical_artifact_path", HISTORICAL, NEW)
    write_rows(output / "results.jsonl", rows)
    write_rows(output / "physical-attempts.jsonl", calls)
    plan = copy.deepcopy(prior_cert["preflight"])
    plan["providers"] = providers
    plan["parent_results_sha256"] = digest(seven / "results.jsonl")
    plan["selection"] = (
        "five shared methods and both independently certified Moli repair variants; "
        "each final physical call was selected before its component correction"
    )
    plan["component_corrections"] = [
        {"role": "prior_repair", "certificate_sha256": digest(prior_correction / "certificate.json")},
        {"role": "content_repair", "certificate_sha256": digest(content_correction / "certificate.json")},
    ]
    certificate = {
        "kind": "webmainbench_origin_correction_v1",
        "preflight": plan,
        "postflight": plan,
        "results_sha256": digest(output / "results.jsonl"),
        "physical_sha256": digest(output / "physical-attempts.jsonl"),
    }
    (output / "certificate.json").write_text(
        json.dumps(certificate, ensure_ascii=False, indent=2) + "\n"
    )
    # Validate the composite through the same public boundary used by report builds.
    validate(output, seven_rows, digest(seven / "results.jsonl"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seven", required=True, type=Path)
    parser.add_argument("--prior-source", required=True, type=Path)
    parser.add_argument("--prior-correction", required=True, type=Path)
    parser.add_argument("--content-source", required=True, type=Path)
    parser.add_argument("--content-correction", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    compose(args.seven, args.prior_source, args.prior_correction,
            args.content_source, args.content_correction, args.output)


if __name__ == "__main__":
    main()
