"""Package only certified, scored WebMainBench-545 evidence for the full report."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path

from harness.web_fetch.safety import contained_path, safe_identifier
from harness.web_fetch.webmainbench_certification import verify_shard
from harness.web_fetch.webmainbench_evidence import (
    alias_row,
    copy_alias_artifacts,
    digest,
    read_jsonl,
    require_matrix,
    require_unique_attempt_ids,
)
from harness.web_fetch.webmainbench_runner import load_source
from harness.web_fetch.webmainbench_scoring import verify_score

ROOT = Path(__file__).resolve().parents[2]


def public_value(value):
    """Remove machine-specific absolute paths from the public evidence copy."""
    if isinstance(value, dict):
        return {key: public_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [public_value(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"/Users/[^/\s:]+", "/redacted-home", value)
        value = re.sub(r"/home/[^/\s:]+", "/redacted-home", value)
        value = re.sub(r"/private/tmp/[^\s:]+", "/redacted-temp", value)
        value = re.sub(r"/(?:var/)?tmp/[^\s:]+", "/redacted-temp", value)
    return value


def copy_bound(
    root: Path,
    output: Path,
    relative: str,
    expected: str,
    *,
    target_relative: str | None = None,
) -> None:
    source = contained_path(root, relative, reject_symlinks=True)
    if (root / relative).is_symlink() or digest(source) != expected:
        raise ValueError("Evidence artifact changed: " + relative)
    target = contained_path(output, target_relative or relative, reject_symlinks=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    if digest(target) != expected:
        raise ValueError("Evidence changed while copying: " + relative)


def _assemble_cohort(
    root: Path,
    output: Path,
    source: Path,
    profile_path: Path,
    *,
    scorer: Path | None = None,
    materialize_sources: bool = True,
) -> None:
    if output.exists():
        raise ValueError("Output already exists; report evidence is immutable")
    if root.is_symlink() or source.is_symlink() or profile_path.is_symlink():
        raise ValueError("Symlinked aggregate input is forbidden")
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    cert_path = root / "cohort-certificate.json"
    certificate = json.loads(cert_path.read_text(encoding="utf-8"))
    scoring_certificate_path = root / "scoring-certificate.json"
    scoring_certificate = json.loads(
        scoring_certificate_path.read_text(encoding="utf-8")
    )
    if (
        certificate.get("kind") != "certified_webmainbench_545_full_v1"
        or certificate.get("profile_sha256") != digest(profile_path)
        or certificate.get("preflight") != certificate.get("postflight")
        or certificate["preflight"]["source_sha256"] != profile["source_dataset_sha256"]
        or [row["provider"] for row in certificate["providers"]] != profile["providers"]
    ):
        raise ValueError("Complete-cohort certificate or profile mismatch")
    if (
        scoring_certificate.get("kind") != "certified_webmainbench_scoring_v1"
        or scoring_certificate.get("cohort_certificate_sha256") != digest(cert_path)
        or scoring_certificate.get("source_binding") != certificate["postflight"]
        or [row["provider"] for row in scoring_certificate["providers"]]
        != profile["providers"]
    ):
        raise ValueError(
            "Complete scoring certificate differs from the certified cohort"
        )
    source_records = load_source(
        source, expected_sha256=profile["source_dataset_sha256"]
    )
    ids = {safe_identifier(row["track_id"]) for row in source_records}
    if len(ids) != len(source_records) or len(source_records) != profile["case_count"]:
        raise ValueError("Source case inventory mismatch")
    results: list[dict] = []
    physical: list[dict] = []
    summaries: dict[str, dict] = {}
    executions: list[dict] = []
    output.mkdir(parents=True)
    (output / "references").mkdir()
    (output / "sources").mkdir()
    (output / "fixture-certificates").mkdir()
    for record in source_records if materialize_sources else []:
        case_id = record["track_id"]
        (output / "references" / (case_id + ".md")).write_text(
            record["groundtruth_content"], encoding="utf-8"
        )
        (output / "sources" / (case_id + ".html.txt")).write_text(
            record["html"], encoding="utf-8"
        )
    for expected, scored_receipt in zip(
        certificate["providers"], scoring_certificate["providers"]
    ):
        provider = expected["provider"]
        shard = contained_path(root, safe_identifier(provider), reject_symlinks=True)
        if shard.is_symlink() or any(path.is_symlink() for path in shard.rglob("*")):
            raise ValueError(provider + ": symlinked evidence is forbidden")
        if verify_shard(shard, provider, ids, profile) != expected:
            raise ValueError(provider + ": certificate differs from measured shard")
        if (
            scorer is not None
            and verify_score(shard, provider, ids, scorer, profile) != scored_receipt
        ):
            raise ValueError(
                provider + ": score certificate differs from verified scores"
            )
        rows = read_jsonl(shard / "scored-results.jsonl")
        if (
            scored_receipt["scored_results_sha256"]
            != digest(shard / "scored-results.jsonl")
            or scored_receipt["score_summary_sha256"]
            != digest(shard / "score-summary.json")
            or scored_receipt["logical_results"] != profile["case_count"]
        ):
            raise ValueError(
                provider + ": scoring certificate differs from scored shard"
            )
        if (
            len(rows) != profile["case_count"]
            or {row["track_id"] for row in rows} != ids
            or len({row["track_id"] for row in rows}) != profile["case_count"]
            or any(row["provider"] != provider for row in rows)
        ):
            raise ValueError(provider + ": incomplete scored matrix")
        summary = json.loads((shard / "score-summary.json").read_text(encoding="utf-8"))
        if (
            summary.get("case_count") != profile["case_count"]
            or summary.get("provider") != provider
            or summary.get("scorer", {}).get("metric_source_sha256")
            != scoring_certificate["scorer_source_sha256"]
        ):
            raise ValueError(provider + ": score summary scope mismatch")
        logical = {row["track_id"]: row for row in read_jsonl(shard / "results.jsonl")}
        for row in rows:
            base = logical[row["track_id"]]
            if {k: v for k, v in row.items() if k != "metrics"} != base:
                raise ValueError(provider + ": scoring changed measured result")
            copy_bound(shard, output, row["artifact_path"], row["output_sha256"])
            results.append(public_value({**row, "case_id": row["track_id"]}))
        attempts = read_jsonl(shard / "physical-attempts.jsonl")
        for attempt in attempts:
            copy_bound(
                shard,
                output,
                attempt["physical_artifact_path"],
                attempt["output_sha256"],
            )
        physical.extend(public_value(attempts))
        execution = json.loads((shard / "execution.json").read_text(encoding="utf-8"))
        ca = execution.get("fixture_ca_artifact")
        if ca:
            ca_source = contained_path(shard, ca, reject_symlinks=True)
            if (
                digest(ca_source)
                != execution["network_isolation_probe"]["fixture_ca_sha256"]
            ):
                raise ValueError(provider + ": fixture CA changed")
            ca_target = output / "fixture-certificates" / (provider + "-ca.pem")
            shutil.copy2(ca_source, ca_target)
            execution["assembled_fixture_ca_path"] = str(ca_target.relative_to(output))
        executions.append(execution)
        summaries[provider] = summary
    expected_pairs = {
        (provider, case_id) for provider in profile["providers"] for case_id in ids
    }
    if (
        len(results) != len(expected_pairs)
        or {(row["provider"], row["case_id"]) for row in results} != expected_pairs
        or len({row["attempt_id"] for row in results}) != len(results)
        or len({row["attempt_id"] for row in physical}) != len(physical)
    ):
        raise ValueError("Full result or physical attempt matrix mismatch")
    cases = [
        {
            "id": row["track_id"],
            "name": row["track_id"],
            "url": row["url"],
            "category": "webmainbench",
            "track": "static_html",
            "meta": row["meta"],
            "input_html_sha256": hashlib.sha256(row["html"].encode()).hexdigest(),
            "reference_sha256": hashlib.sha256(
                row["groundtruth_content"].encode()
            ).hexdigest(),
            "reference_chars": len(row["groundtruth_content"]),
        }
        for row in source_records
    ]
    dataset = {
        "schema_version": 1,
        "tool": "web_fetch",
        "id": profile["dataset_id"],
        "name": f"WebMainBench · {len(ids)} tasks · {len(profile['providers'])} methods",
        "source_dataset_sha256": profile["source_dataset_sha256"],
        "cases": cases,
        "coverage_plan": {"static_html": profile["providers"]},
        "comparison": profile,
    }
    (output / "dataset.json").write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "score-summaries.json").write_text(
        json.dumps(summaries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "execution.json").write_text(
        json.dumps({"batches": executions}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output / "results.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in results),
        encoding="utf-8",
    )
    (output / "physical-attempts.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in physical),
        encoding="utf-8",
    )
    shutil.copy2(cert_path, output / "cohort-certificate.json")
    shutil.copy2(scoring_certificate_path, output / "scoring-certificate.json")
    shutil.copy2(profile_path, output / "current-report.json")


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _write_rows(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _cohort_input_files(
    root: Path, profile_path: Path, scorer: Path | None
) -> set[Path]:
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    paths = {profile_path}
    for name in ("cohort-certificate.json", "scoring-certificate.json"):
        paths.add(contained_path(root, name, reject_symlinks=True))
    scoring = json.loads(
        (root / "scoring-certificate.json").read_text(encoding="utf-8")
    )
    if scorer is not None:
        paths.update(
            contained_path(scorer, name, reject_symlinks=True)
            for name in scoring["scorer_source_sha256"]
        )
    for provider in profile["providers"]:
        shard = contained_path(root, safe_identifier(provider), reject_symlinks=True)
        for name in (
            "results.jsonl",
            "physical-attempts.jsonl",
            "scored-results.jsonl",
            "score-summary.json",
            "execution.json",
            "dataset.json",
        ):
            paths.add(contained_path(shard, name, reject_symlinks=True))
        for name, key in (
            ("results.jsonl", "artifact_path"),
            ("physical-attempts.jsonl", "physical_artifact_path"),
        ):
            paths.update(
                contained_path(shard, row[key], reject_symlinks=True)
                for row in read_jsonl(shard / name)
            )
        execution = json.loads((shard / "execution.json").read_text(encoding="utf-8"))
        if execution.get("fixture_ca_artifact"):
            paths.add(
                contained_path(
                    shard, execution["fixture_ca_artifact"], reject_symlinks=True
                )
            )
    return paths


def assemble(
    root: Path,
    output: Path,
    source: Path,
    profile_path: Path,
    *,
    registry_path: Path | None = None,
    cohorts: dict[str, Path] | None = None,
    scorer: Path | None = None,
) -> None:
    """Assemble the declared baseline and independently certified variant cohorts."""
    if output.exists():
        raise ValueError("Output already exists; report evidence is immutable")
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    providers = [safe_identifier(provider) for provider in profile["providers"]]
    if not providers or len(providers) != len(set(providers)):
        raise ValueError("Baseline providers must be nonempty and unique")
    variants = []
    cohorts = cohorts or {}
    if registry_path is not None:
        variants = json.loads(registry_path.read_text(encoding="utf-8"))["variants"]
        names = [safe_identifier(item["provider"]) for item in variants]
        profiles = [item["profile"] for item in variants]
        metadata_names = profiles + [
            name
            for provider in names
            for name in (
                f"{provider}-cohort-certificate.json",
                f"{provider}-scoring-certificate.json",
            )
        ]
        reserved = {
            "current-report.json",
            "cohort-certificate.json",
            "scoring-certificate.json",
            "dataset.json",
            "execution.json",
            "score-summaries.json",
            "method-binding.json",
        }
        if (
            len(metadata_names) != len(set(metadata_names))
            or set(metadata_names) & reserved
        ):
            raise ValueError("Variant metadata filenames collide")
        if (
            len(names) != len(set(names))
            or set(names) != set(cohorts)
            or set(names) & set(profile["providers"])
            or len(profiles) != len(set(profiles))
        ):
            raise ValueError("Variant registry and complete cohort inventory differ")
        if not variants or scorer is None:
            raise ValueError(
                "Variant aggregation requires registered cohorts and a pinned scorer"
            )
    elif cohorts:
        raise ValueError("Variant cohorts require an explicit registry")
    for path in (
        root,
        source,
        profile_path,
        *cohorts.values(),
        *([registry_path] if registry_path else []),
    ):
        if path.is_symlink():
            raise ValueError("Symlinked aggregate input is forbidden")
    for path in (root, *cohorts.values()):
        if any(child.is_symlink() for child in path.rglob("*")):
            raise ValueError("Symlinked aggregate evidence is forbidden")
        if output.resolve().is_relative_to(path.resolve()):
            raise ValueError("Aggregate output cannot be inside input evidence")
    inputs = _cohort_input_files(root, profile_path, scorer) | {source}
    if registry_path is not None:
        inputs.add(registry_path)
        for item in variants:
            variant_profile_path = contained_path(
                registry_path.parent, item["profile"], reject_symlinks=True
            )
            inputs.update(
                _cohort_input_files(
                    cohorts[item["provider"]], variant_profile_path, scorer
                )
            )
    fingerprints = {path: digest(path) for path in inputs}
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".webmainbench-assemble-", dir=output.parent
    ) as work:
        staging = Path(work) / "package"
        _assemble_cohort(root, staging, source, profile_path, scorer=scorer)
        baseline_cert = json.loads((staging / "cohort-certificate.json").read_text())
        rows = read_jsonl(staging / "results.jsonl")
        calls = read_jsonl(staging / "physical-attempts.jsonl")
        summaries = json.loads((staging / "score-summaries.json").read_text())
        execution = json.loads((staging / "execution.json").read_text())
        dataset = json.loads((staging / "dataset.json").read_text())
        providers = list(profile["providers"])
        bound_variants = []
        for index, item in enumerate(variants):
            provider = item["provider"]
            candidate = cohorts[provider]
            variant_profile_path = contained_path(
                registry_path.parent, item["profile"], reject_symlinks=True
            )
            variant_profile = json.loads(variant_profile_path.read_text())
            source_provider = item.get("source_provider")
            if source_provider is None and len(variant_profile["providers"]) == 1:
                source_provider = variant_profile["providers"][0]
            if variant_profile["providers"] != [source_provider]:
                raise ValueError(
                    "A variant profile must declare exactly its selected source provider"
                )
            safe_identifier(source_provider)
            for key in (
                "case_count",
                "case_ids_sha256",
                "source_dataset_sha256",
                "official_scorer_commit",
                "metric_config",
            ):
                if variant_profile.get(key) != profile.get(key):
                    raise ValueError("Variant and baseline contracts differ: " + key)
            cert = json.loads((candidate / "cohort-certificate.json").read_text())
            if cert.get("preflight") != baseline_cert["postflight"]:
                raise ValueError("Variant and baseline source bindings differ")
            if variant_profile.get("binary_sha256") and (
                cert.get("binary_source_commit") != variant_profile.get("source_commit")
                or cert.get("measured_binary_sha256")
                != variant_profile["binary_sha256"]
            ):
                raise ValueError("Variant binary differs from its certified profile")
            intermediate = Path(work) / f"cohort-{index}"
            _assemble_cohort(
                candidate,
                intermediate,
                source,
                variant_profile_path,
                scorer=scorer,
                materialize_sources=False,
            )
            variant_rows = read_jsonl(intermediate / "results.jsonl")
            variant_calls = read_jsonl(intermediate / "physical-attempts.jsonl")
            copy_alias_artifacts(
                intermediate,
                staging,
                variant_rows + variant_calls,
                source_provider,
                provider,
            )
            rows.extend(
                alias_row(row, source_provider, provider) for row in variant_rows
            )
            calls.extend(
                alias_row(row, source_provider, provider) for row in variant_calls
            )
            summary = json.loads((intermediate / "score-summaries.json").read_text())[
                source_provider
            ]
            summaries[provider] = {**summary, "provider": provider}
            batch = json.loads((intermediate / "execution.json").read_text())[
                "batches"
            ][0]
            if (
                variant_profile.get("binary_sha256")
                and batch["implementation_identity"]["binary_sha256"]
                != variant_profile["binary_sha256"]
            ):
                raise ValueError("Variant execution binary differs from profile")
            batch["provider"] = provider
            if batch.get("assembled_fixture_ca_path"):
                original_ca = batch["assembled_fixture_ca_path"]
                target_ca = f"fixture-certificates/{provider}-ca.pem"
                copy_bound(
                    intermediate,
                    staging,
                    original_ca,
                    batch["network_isolation_probe"]["fixture_ca_sha256"],
                    target_relative=target_ca,
                )
                batch["assembled_fixture_ca_path"] = target_ca
            execution["batches"].append(batch)
            certificate_name = f"{provider}-cohort-certificate.json"
            scoring_name = f"{provider}-scoring-certificate.json"
            profile_name = item["profile"]
            if Path(profile_name).name in {
                "current-report.json",
                "dataset.json",
                "method-binding.json",
            }:
                raise ValueError("Variant profile collides with aggregate metadata")
            for source_path, relative in (
                (variant_profile_path, profile_name),
                (candidate / "cohort-certificate.json", certificate_name),
                (candidate / "scoring-certificate.json", scoring_name),
            ):
                target = contained_path(staging, relative, reject_symlinks=True)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, target)
            bound_variants.append(
                {
                    "provider": provider,
                    "source_provider": source_provider,
                    "role": item.get("role", provider),
                    "profile": profile_name,
                    "profile_sha256": digest(variant_profile_path),
                    "cohort_certificate": certificate_name,
                    "cohort_certificate_sha256": digest(
                        candidate / "cohort-certificate.json"
                    ),
                    "scoring_certificate": scoring_name,
                    "scoring_certificate_sha256": digest(
                        candidate / "scoring-certificate.json"
                    ),
                    "source_results_sha256": digest(
                        candidate / source_provider / "scored-results.jsonl"
                    ),
                }
            )
            providers.append(provider)
        ids = {case["id"] for case in dataset["cases"]}
        require_matrix(rows, providers, ids)
        require_unique_attempt_ids(calls, "Physical call")
        _write_rows(staging / "results.jsonl", rows)
        _write_rows(staging / "physical-attempts.jsonl", calls)
        _write_json(staging / "score-summaries.json", summaries)
        _write_json(staging / "execution.json", execution)
        dataset["coverage_plan"]["static_html"] = providers
        dataset["name"] = f"WebMainBench · {len(ids)} tasks · {len(providers)} methods"
        _write_json(staging / "dataset.json", dataset)
        if registry_path is not None:
            _write_json(
                staging / "method-binding.json",
                {
                    "schema_version": 2,
                    "kind": "webmainbench_method_cohort_binding",
                    "registry_sha256": digest(registry_path),
                    "baseline_profile_sha256": digest(profile_path),
                    "baseline_certificate_sha256": digest(
                        root / "cohort-certificate.json"
                    ),
                    "variants": bound_variants,
                    "results_sha256": digest(staging / "results.jsonl"),
                    "physical_attempts_sha256": digest(
                        staging / "physical-attempts.jsonl"
                    ),
                    "logical_results": len(rows),
                    "physical_calls": len(calls),
                },
            )
        if any(
            path.is_symlink() or digest(path) != expected
            for path, expected in fingerprints.items()
        ):
            raise ValueError("Declared evidence changed during aggregation")
        staging.rename(output)
    print(f"Assembled {len(rows)} results and {len(calls)} physical calls", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument(
        "--profile",
        type=Path,
        default=ROOT / "evaluation/webmainbench-full/current-report.json",
    )
    parser.add_argument(
        "--variants", type=Path, help="Variant registry bound by the report profile"
    )
    parser.add_argument(
        "--cohort", action="append", default=[], metavar="PROVIDER=PATH"
    )
    parser.add_argument(
        "--scorer", type=Path, help="Pinned scorer checkout; required with variants"
    )
    args = parser.parse_args()
    cohorts = {}
    for value in args.cohort:
        name, separator, path = value.partition("=")
        if not separator or not path or name in cohorts:
            parser.error("Each cohort needs a unique PROVIDER=PATH binding")
        cohorts[safe_identifier(name)] = Path(path)
    assemble(
        args.source_root,
        args.output,
        args.dataset,
        args.profile,
        registry_path=args.variants,
        cohorts=cohorts,
        scorer=args.scorer,
    )


if __name__ == "__main__":
    main()
