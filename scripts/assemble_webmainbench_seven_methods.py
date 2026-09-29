"""Join the baseline and two independently certified Moli repairs.

The two repair cohorts use the historical provider key ``moli-fixed`` in
their private evidence. This assembler gives each cohort a stable public
identity without changing any measured output or physical call.
"""
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
from scripts.run_certified_webmainbench_full import digest, read_jsonl


def write_rows(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))


def alias_row(row: dict, source_provider: str, provider: str) -> dict:
    row = copy.deepcopy(row)
    row["provider"] = provider
    row["attempt_id"] = row["attempt_id"].replace(source_provider + "__", provider + "__", 1)
    for key in ("artifact_path", "physical_artifact_path"):
        if key in row:
            row[key] = row[key].replace(
                f"artifacts/{source_provider}/", f"artifacts/{provider}/", 1
            )
    if row.get("supersedes_attempt_id"):
        row["supersedes_attempt_id"] = row["supersedes_attempt_id"].replace(
            source_provider + "__", provider + "__", 1
        )
    return row


def copy_alias_artifacts(source: Path, output: Path, rows: list[dict],
                         source_provider: str, provider: str) -> None:
    for row in rows:
        expected_sha256 = row.get("output_sha256")
        if not isinstance(expected_sha256, str) or not expected_sha256:
            raise ValueError(
                "Variant artifact is missing output_sha256: "
                + str(row.get("attempt_id", "unknown attempt"))
            )
        for key in ("artifact_path", "physical_artifact_path"):
            if key not in row:
                continue
            original = contained_path(source, row[key])
            if digest(original) != expected_sha256:
                raise ValueError("Variant artifact changed: " + row[key])
            relative = row[key].replace(
                f"artifacts/{source_provider}/", f"artifacts/{provider}/", 1
            )
            target = contained_path(output, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, target)


def require_matrix(rows: list[dict], providers: list[str], case_ids: set[str]) -> None:
    expected = {(provider, case_id) for provider in providers for case_id in case_ids}
    actual = {(row["provider"], row["case_id"]) for row in rows}
    attempts = [row["attempt_id"] for row in rows]
    if actual != expected or len(rows) != len(expected):
        raise ValueError("Seven-method logical result matrix is incomplete")
    if len(attempts) != len(set(attempts)):
        raise ValueError("Seven-method logical attempt identity is not unique")


def require_provider_batch(execution: dict, provider: str) -> dict:
    batches = [batch for batch in execution.get("batches", [])
               if batch.get("provider") == provider]
    if len(batches) != 1:
        raise ValueError(
            f"Expected exactly one execution batch for {provider}, found {len(batches)}"
        )
    return copy.deepcopy(batches[0])


def drop_inherited_teds_revision(output: Path) -> None:
    """A newly assembled matrix needs one correction over its complete scope."""
    for name in ("teds-correction.json", "teds-correction-review.json",
                 "original-results.jsonl"):
        (output / name).unlink(missing_ok=True)
    shutil.rmtree(output / "correction-source", ignore_errors=True)


def assemble(prior: Path, content: Path, output: Path) -> None:
    if output.exists():
        raise ValueError("Composite evidence is immutable")
    registry_path = ROOT / "evaluation/webmainbench-full/moli-variants.json"
    registry = json.loads(registry_path.read_text())
    variants = registry["variants"]
    if [item["role"] for item in variants] != ["prior_repair", "content_repair"]:
        raise ValueError("Moli repair registry order or roles changed")
    sources = {"prior_repair": prior, "content_repair": content}
    source_rows = {role: read_jsonl(path / "results.jsonl") for role, path in sources.items()}
    base_providers = json.loads((prior / "current-report.json").read_text())["providers"]
    case_ids = {item["id"] for item in json.loads((prior / "dataset.json").read_text())["cases"]}
    for provider in base_providers:
        old = [row for row in source_rows["prior_repair"] if row["provider"] == provider]
        new = [row for row in source_rows["content_repair"] if row["provider"] == provider]
        if old != new:
            raise ValueError("Shared baseline differs between repair cohorts: " + provider)
    shutil.copytree(prior, output)
    drop_inherited_teds_revision(output)
    prior_alias = variants[0]
    content_alias = variants[1]
    historical = prior_alias["evidence_alias"]
    old_variant_rows = [row for row in source_rows["prior_repair"] if row["provider"] == historical]
    old_variant_calls = [row for row in read_jsonl(prior / "physical-attempts.jsonl")
                         if row["provider"] == historical]
    new_variant_rows = [row for row in source_rows["content_repair"] if row["provider"] == historical]
    new_variant_calls = [row for row in read_jsonl(content / "physical-attempts.jsonl")
                         if row["provider"] == historical]
    copy_alias_artifacts(prior, output, old_variant_rows + old_variant_calls,
                         historical, prior_alias["provider"])
    copy_alias_artifacts(content, output, new_variant_rows + new_variant_calls,
                         historical, content_alias["provider"])
    # The copied prior tree still contains the historical moli-fixed artifact
    # directory. It has been materialized under its stable public identity.
    shutil.rmtree(output / "artifacts" / historical)
    baseline_rows = [row for row in source_rows["prior_repair"] if row["provider"] in base_providers]
    combined = baseline_rows
    combined += [alias_row(row, historical, prior_alias["provider"]) for row in old_variant_rows]
    combined += [alias_row(row, historical, content_alias["provider"]) for row in new_variant_rows]
    providers = base_providers + [item["provider"] for item in variants]
    require_matrix(combined, providers, case_ids)
    write_rows(output / "results.jsonl", combined)
    baseline_calls = [row for row in read_jsonl(prior / "physical-attempts.jsonl")
                      if row["provider"] in base_providers]
    physical = baseline_calls
    physical += [alias_row(row, historical, prior_alias["provider"]) for row in old_variant_calls]
    physical += [alias_row(row, historical, content_alias["provider"]) for row in new_variant_calls]
    attempts = [row["attempt_id"] for row in physical]
    if len(attempts) != len(set(attempts)):
        raise ValueError("Seven-method physical attempt identity is not unique")
    write_rows(output / "physical-attempts.jsonl", physical)
    summaries = json.loads((prior / "score-summaries.json").read_text())
    old_summary = summaries.pop(historical)
    summaries[prior_alias["provider"]] = {**old_summary, "provider": prior_alias["provider"]}
    new_summary = json.loads((content / "score-summaries.json").read_text())[historical]
    summaries[content_alias["provider"]] = {**new_summary, "provider": content_alias["provider"]}
    (output / "score-summaries.json").write_text(
        json.dumps(summaries, ensure_ascii=False, indent=2) + "\n"
    )
    execution = json.loads((prior / "execution.json").read_text())
    for batch in execution["batches"]:
        if batch.get("provider") == historical:
            batch["provider"] = prior_alias["provider"]
    content_batch = require_provider_batch(
        json.loads((content / "execution.json").read_text()), historical
    )
    content_batch["provider"] = content_alias["provider"]
    execution["batches"].append(content_batch)
    (output / "execution.json").write_text(json.dumps(execution, ensure_ascii=False, indent=2) + "\n")
    dataset = json.loads((output / "dataset.json").read_text())
    dataset["coverage_plan"]["static_html"] = providers
    dataset["name"] = "WebMainBench-545 full · seven Web Fetch methods"
    (output / "dataset.json").write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n")
    # Replace the ambiguous historical six-method files with two explicit,
    # independently bound repair records.
    for name in ("moli-fixed-report.json", "fixed-cohort-certificate.json",
                 "fixed-scoring-certificate.json", "six-method-binding.json"):
        (output / name).unlink(missing_ok=True)
    bound_variants = []
    for item in variants:
        source = sources[item["role"]]
        profile_path = ROOT / "evaluation/webmainbench-full" / item["profile"]
        prefix = item["provider"]
        shutil.copy2(profile_path, output / item["profile"])
        shutil.copy2(source / "fixed-cohort-certificate.json", output / f"{prefix}-cohort-certificate.json")
        shutil.copy2(source / "fixed-scoring-certificate.json", output / f"{prefix}-scoring-certificate.json")
        bound_variants.append({
            "provider": item["provider"],
            "role": item["role"],
            "profile": item["profile"],
            "profile_sha256": digest(profile_path),
            "cohort_certificate": f"{prefix}-cohort-certificate.json",
            "cohort_certificate_sha256": digest(source / "fixed-cohort-certificate.json"),
            "scoring_certificate": f"{prefix}-scoring-certificate.json",
            "scoring_certificate_sha256": digest(source / "fixed-scoring-certificate.json"),
            "source_results_sha256": digest(source / "results.jsonl"),
            "source_six_method_binding_sha256": digest(source / "six-method-binding.json"),
        })
    binding = {
        "schema_version": 1,
        "kind": "webmainbench_seven_method_full_scope_binding",
        "registry_sha256": digest(registry_path),
        "baseline_profile_sha256": digest(ROOT / "evaluation/webmainbench-full/current-report.json"),
        "baseline_certificate_sha256": digest(prior / "cohort-certificate.json"),
        "variants": bound_variants,
        "results_sha256": digest(output / "results.jsonl"),
        "physical_attempts_sha256": digest(output / "physical-attempts.jsonl"),
        "logical_results": len(combined),
        "physical_calls": len(physical),
    }
    (output / "seven-method-binding.json").write_text(
        json.dumps(binding, ensure_ascii=False, indent=2) + "\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prior", required=True, type=Path)
    parser.add_argument("--content", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    assemble(args.prior, args.content, args.output)


if __name__ == "__main__":
    main()
