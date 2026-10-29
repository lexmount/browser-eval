"""Construct, execute and validate the profile-bound risk-control cohort."""

from __future__ import annotations

from harness.distribution import source_path

from harness.distribution import repository_root

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lexbench_webfetch.paths import contained_path

from harness.web_fetch.artifacts import diagnostic_log_binding
from harness.web_fetch.config import load_dataset, load_provider_config, load_runtime_env
from harness.web_fetch.reference_inputs import benchmark_input_path, read_dataset_bytes
from harness.web_fetch.retrieval_hints import plan_for_case
from harness.web_fetch.storage import data_home


def _digest(path: Path) -> str:
    return hashlib.sha256(benchmark_input_path(path).read_bytes()).hexdigest()


def _case_ids_digest(case_ids: list[str]) -> str:
    return hashlib.sha256(("\n".join(case_ids) + "\n").encode()).hexdigest()


RISK_CONTROL_CERTIFICATIONS = {
    "certified_risk_control_100_moli_v1": (
        "reference-proof-verification.json", "reference_proof_verification_sha256"),
    "certified_risk_control_100_moli_v2": (
        "frozen-input-verification.json", "frozen_input_verification_sha256"),
}


def risk_control_verification_binding(receipt: dict) -> tuple[str, str]:
    try:
        return RISK_CONTROL_CERTIFICATIONS[receipt["kind"]]
    except (KeyError, TypeError) as error:
        raise ValueError("Unsupported risk-control certification") from error


def frozen_risk_control_input_snapshot(profile_path: Path) -> dict:
    """Verify executable inputs without reopening historical source qualification."""
    root = profile_path.resolve().parents[2]
    profile_raw = profile_path.read_bytes()
    profile = json.loads(profile_raw)
    files = [{"role": "profile", "path": profile_path.resolve().relative_to(root).as_posix(),
              "sha256": hashlib.sha256(profile_raw).hexdigest(), "bytes": len(profile_raw)}]
    bindings = {"dataset": profile["dataset"],
                "provider_config": {"path": profile["provider"]["config_path"],
                                    "sha256": profile["provider"]["config_sha256"]},
                **profile["source_bindings"]}
    for role, binding in sorted(bindings.items()):
        path = contained_path(root, binding["path"])
        if path.is_symlink():
            raise ValueError("Missing frozen input: " + binding["path"])
        raw = read_dataset_bytes(path) if role == 'dataset' else benchmark_input_path(path).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != binding["sha256"]:
            raise ValueError("Changed frozen input: " + binding["path"])
        files.append({"role": role, "path": binding["path"], "sha256": digest, "bytes": len(raw)})
    dataset = json.loads(read_dataset_bytes(contained_path(root, profile["dataset"]["path"])))
    inventory = json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {"case_count": len(dataset["cases"]),
            "inventory_sha256": hashlib.sha256(inventory.encode()).hexdigest(), "files": files}


def risk_control_comparison(profile: dict[str, Any], profile_sha256: str) -> dict[str, Any]:
    execution = profile["execution"]
    return {
        "kind": "risk_control_100_moli",
        "run_profile_sha256": profile_sha256,
        "timeout_seconds": execution["timeout_seconds"],
        "runs_per_case": execution["runs_per_case"],
        "concurrency": execution["concurrency"],
        "max_failure_retries": execution["max_failure_retries"],
        "conversion": "Moli native Markdown; wait-until done; no per-case options",
        "require_independent_result_review": False,
    }


def measured_risk_control_dataset(
    profile: dict[str, Any], frozen_dataset: dict[str, Any], profile_sha256: str
) -> dict[str, Any]:
    return {
        **frozen_dataset,
        "coverage_plan": {"risk_control": [profile["provider"]["name"]]},
        "comparison": risk_control_comparison(profile, profile_sha256),
    }


def validate_risk_control_certification(
    source: Path,
    dataset: dict[str, Any],
    records: list[dict[str, Any]],
    receipt: dict[str, Any],
    profile_path: Path,
) -> None:
    """Reject any evidence that is not the exact profile-bound 100-by-1 matrix."""
    profile_bytes = profile_path.read_bytes()
    profile_sha256 = hashlib.sha256(profile_bytes).hexdigest()
    profile = json.loads(profile_bytes)
    repository_root = profile_path.parents[2]
    frozen_dataset_path = contained_path(repository_root, profile["dataset"]["path"])
    frozen_dataset_bytes = read_dataset_bytes(frozen_dataset_path)
    frozen_dataset_sha256 = hashlib.sha256(frozen_dataset_bytes).hexdigest()
    frozen_dataset = json.loads(frozen_dataset_bytes)
    expected_case_ids = [case["id"] for case in frozen_dataset["cases"]]
    provider_name = profile["provider"]["name"]
    expected_identity = {
        "reported_version": profile["provider"]["reported_version"],
        "binary_sha256": profile["provider"]["binary_sha256"],
    }
    execution = profile["execution"]
    if (
        profile.get("schema_version") != 1
        or profile.get("kind") != "risk_control_100_moli_v1"
        or frozen_dataset_sha256 != profile["dataset"]["sha256"]
        or (len(expected_case_ids) != profile["dataset"]["case_count"])
        or (len(expected_case_ids) != 100)
        or (len(expected_case_ids) != len(set(expected_case_ids)))
        or (_case_ids_digest(expected_case_ids) != profile["dataset"]["case_ids_sha256"])
        or (frozen_dataset.get("site_count") != profile["dataset"]["site_count"])
    ):
        raise ValueError("Risk-control profile or frozen dataset is inconsistent")
    measured_base = {
        key: value for key, value in dataset.items() if key not in {"coverage_plan", "comparison"}
    }
    if (
        measured_base != frozen_dataset
        or dataset.get("coverage_plan") != {"risk_control": [provider_name]}
        or dataset.get("comparison") != risk_control_comparison(profile, profile_sha256)
    ):
        raise ValueError("Risk-control measured dataset does not match the frozen profile")
    expected_pairs = {(provider_name, case_id) for case_id in expected_case_ids}
    observed_pairs = [(record.get("provider"), record.get("case_id")) for record in records]
    expected_attempt_ids = [f"{provider_name}__{case_id}__r001" for case_id in expected_case_ids]
    if (
        len(records) != 100
        or len(observed_pairs) != len(set(observed_pairs))
        or set(observed_pairs) != expected_pairs
        or ([record.get("attempt_id") for record in records] != expected_attempt_ids)
        or any(record.get("run") != 1 for record in records)
    ):
        raise ValueError("Risk-control results are not the complete unique 100-by-1 matrix")
    for record in records:
        metadata = record.get("provider_metadata", {})
        if (
            metadata.get("reported_version") != expected_identity["reported_version"]
            or metadata.get("binary_sha256") != expected_identity["binary_sha256"]
            or "retry" in metadata
        ):
            raise ValueError("Risk-control result provider identity or retry policy changed")
    manifest_path = contained_path(source, "manifest.json")
    execution_path = contained_path(source, "execution.json")
    verification_name, verification_field = risk_control_verification_binding(receipt)
    verification_path = contained_path(source, verification_name)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    execution_record = json.loads(execution_path.read_text(encoding="utf-8"))
    proof_verification = json.loads(verification_path.read_text(encoding="utf-8"))
    if receipt["kind"] == "certified_risk_control_100_moli_v2":
        expected_inputs = frozen_risk_control_input_snapshot(profile_path)
        if (proof_verification.get("schema_version") != 1
                or proof_verification.get("kind") != "risk_control_frozen_input_verification"
                or proof_verification.get("unchanged") is not True
                or proof_verification.get("before") != expected_inputs
                or proof_verification.get("after") != expected_inputs):
            raise ValueError("Risk-control frozen input verification is incomplete or mismatched")
    else:
        proof_rows = proof_verification.get("before", {}).get("files", [])
        expected_proofs = [
            {
                "case_id": case["id"],
                **case["source_benchmark"]["risk_control"]["reference_proof"],
            }
            for case in frozen_dataset["cases"]
        ]
        observed_proofs = [
            {key: row.get(key) for key in ("case_id", "root", "path", "sha256")}
            for row in proof_rows
        ]
        proof_inventory = json.dumps(
            proof_rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        if (
            proof_verification.get("schema_version") != 1
            or proof_verification.get("kind") != "risk_control_reference_proof_verification"
            or proof_verification.get("unchanged") is not True
            or proof_verification.get("after") != proof_verification.get("before")
            or proof_verification.get("before", {}).get("case_count") != 100
            or proof_verification.get("before", {}).get("inventory_sha256")
            != hashlib.sha256(proof_inventory.encode()).hexdigest()
            or observed_proofs != expected_proofs
            or any(type(row.get("bytes")) is not int or row["bytes"] < 1 for row in proof_rows)
        ):
            raise ValueError("Risk-control reference proof verification is incomplete or mismatched")
    proof_verification_sha256 = _digest(verification_path)
    provider_rows = manifest.get("providers")
    parameters = manifest.get("parameters", {})
    if (
        manifest.get("attempt_count") != 100
        or manifest.get("attempt_ids") != expected_attempt_ids
        or manifest.get("unexecuted") != []
        or manifest.get("dataset_sha256") != frozen_dataset_sha256
        or manifest.get("provider_config_sha256") != profile["provider"]["config_sha256"]
        or manifest.get(verification_field)
        != proof_verification_sha256
        or not isinstance(provider_rows, list)
        or len(provider_rows) != 1
        or provider_rows[0].get("name") != provider_name
        or provider_rows[0].get("implementation_identity") != expected_identity
        or parameters.get("runs") != execution["runs_per_case"]
        or parameters.get("timeout") != execution["timeout_seconds"]
        or parameters.get("delay") != execution["delay_seconds"]
        or parameters.get("provider_concurrency") != execution["concurrency"]
        or parameters.get("max_failure_retries") != execution["max_failure_retries"]
    ):
        raise ValueError("Risk-control manifest does not match the frozen execution contract")
    aggregate = execution_record.get("aggregate", {})
    if (
        execution_record.get("schema_version") != 1
        or execution_record.get("batches") != [manifest]
        or aggregate.get("providers") != [provider_name]
        or (aggregate.get("case_ids") != expected_case_ids)
        or (aggregate.get("batch_count") != 1)
        or (aggregate.get("started_at") != manifest.get("started_at"))
        or (not isinstance(aggregate.get("completed_at"), str))
        or (not aggregate["completed_at"])
    ):
        raise ValueError("Risk-control execution record is incomplete or mismatched")
    results_path = contained_path(source, "results.jsonl")
    if (
        receipt.get("schema_version") != 1
        or receipt.get("kind") not in RISK_CONTROL_CERTIFICATIONS
        or receipt.get("profile_sha256") != profile_sha256
        or receipt.get("dataset_sha256") != _digest(contained_path(source, "dataset.json"))
        or receipt.get("results_sha256") != _digest(results_path)
        or receipt.get("manifest_sha256") != _digest(manifest_path)
        or receipt.get("execution_sha256") != _digest(execution_path)
        or receipt.get(verification_field)
        != proof_verification_sha256
        or receipt.get("provider_identity_before") != expected_identity
        or receipt.get("provider_identity_after") != expected_identity
        or receipt.get("logical_results") != 100
        or receipt.get("physical_calls") != 100
        or receipt.get("complete_diagnostic_logs") is not True
    ):
        raise ValueError("Risk-control certification receipt is incomplete or mismatched")


ROOT = repository_root()
DEFAULT_PROFILE = (ROOT / "evaluation/risk-control/run-profile.json")
PROXY_VARIABLES = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)


def rooted(value: str) -> Path:
    path = Path(os.path.expandvars(value)).expanduser()
    return path.resolve() if path.is_absolute() else (source_path(path, root=ROOT)).resolve()


def implementation_identity(provider, *, refresh: bool = False) -> dict:
    if refresh or not isinstance(getattr(provider, "_implementation_identity", None), dict):
        if errors := provider.validate():
            raise ValueError("Moli identity/configuration error: " + ", ".join(errors))
    return dict(provider._implementation_identity)


def require_complete_diagnostic_logs(records: list[dict]) -> None:
    """Fail closed unless every logical call retained its complete raw stderr."""
    incomplete = [
        row["case_id"]
        for row in records
        if diagnostic_log_binding(row.get("provider_metadata", {})) is None
        or row.get("provider_metadata", {}).get("diagnostic_log_truncated") is not False
    ]
    if incomplete:
        raise ValueError(
            "Every Moli call must retain complete hash-bound raw stderr; incomplete cases: "
            + ", ".join(incomplete)
        )


def validate(profile_path: Path) -> tuple[dict, dict, list, object, Path]:
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    if profile.get("schema_version") != 1 or profile.get("kind") != "risk_control_100_moli_v1":
        raise ValueError("Unsupported risk-control run profile")
    for binding in profile["source_bindings"].values():
        path = rooted(binding["path"])
        if not path.is_file() or _digest(path) != binding["sha256"]:
            raise ValueError("Frozen source binding changed: " + binding["path"])
    dataset_path = rooted(profile["dataset"]["path"])
    if hashlib.sha256(read_dataset_bytes(dataset_path)).hexdigest() != profile["dataset"]["sha256"]:
        raise ValueError("Frozen risk-control dataset changed")
    dataset, cases = load_dataset(dataset_path)
    ids = [case.id for case in cases]
    if (
        len(cases) != profile["dataset"]["case_count"]
        or dataset.get("site_count") != profile["dataset"]["site_count"]
        or _case_ids_digest(ids) != profile["dataset"]["case_ids_sha256"]
    ):
        raise ValueError("Frozen risk-control case inventory changed")
    if any(
        case.track != "risk_control"
        or case.task_kind != "body"
        or case.retrieval_hints
        or (plan_for_case(case) is not None)
        for case in cases
    ):
        raise ValueError("Every risk-control case must use the same ordinary Markdown route")
    execution = profile["execution"]
    if execution != {
        "certified_entrypoint": "scripts/run_certified_benchmark.py risk-control",
        "runs_per_case": 1,
        "timeout_seconds": 45,
        "delay_seconds": 0.5,
        "concurrency": 1,
        "max_failure_retries": 0,
        "output_contract": "native_markdown",
        "wait_until": "done",
        "network": "direct; proxy environment removed",
        "locale": "C",
        "only_per_case_argument": "url",
        "log_level": "debug",
        "max_output_bytes": 20000000,
        "retain_complete_diagnostic_log": True,
        "max_diagnostic_bytes": 100000000,
        "trace_network": False,
        "diagnostic_log_policy": "retain_complete_raw_stderr_for_every_call_outside_git",
        "report_diagnostic_log_access": "local_private_file_link",
    }:
        raise ValueError("Risk-control execution parameters changed")
    config_path = rooted(profile["provider"]["config_path"])
    if _digest(config_path) != profile["provider"]["config_sha256"]:
        raise ValueError("Frozen Moli provider configuration changed")
    config = load_provider_config(config_path)
    provider_name = profile["provider"]["name"]
    if list(config) != [provider_name]:
        raise ValueError("Risk-control provider configuration must contain one Moli method")

    # Certificate validation does not load the provider execution stack.
    from harness.web_fetch.providers import build_provider

    provider = build_provider(provider_name, config[provider_name])
    if errors := provider.validate():
        raise ValueError("Moli identity/configuration error: " + ", ".join(errors))
    identity = implementation_identity(provider)
    if identity != {
        "reported_version": profile["provider"]["reported_version"],
        "binary_sha256": profile["provider"]["binary_sha256"],
    }:
        raise ValueError("Moli implementation identity changed")
    if rooted(profile["provider"]["binary_path"]) != Path(provider._resolve_binary()).resolve():
        raise ValueError("Moli binary path changed")
    return (profile, dataset, cases, provider, config_path)


def main(argv: list[str] | None = None, *, certified: bool = False) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Load runtime paths from the repository .env or another explicit file",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--no-report", action="store_true",
                        help="Save certified evidence without building local HTML")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Make the 100 provider calls; omit for a call-free validation",
    )
    args = parser.parse_args(argv)
    if args.execute and (not certified):
        parser.error("Formal execution must use scripts/run_certified_benchmark.py risk-control")
    if args.env_file:
        load_runtime_env(args.env_file)
    profile_path = args.profile.resolve()
    profile, dataset, cases, provider, config_path = validate(profile_path)
    if not args.execute:
        print(
            json.dumps(
                {
                    "status": profile["status"],
                    "unresolved_case_ids": profile["unresolved_case_ids"],
                    "cases": len(cases),
                    "provider": implementation_identity(provider),
                    "provider_calls": 0,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if profile["status"] != "ready" or profile["unresolved_case_ids"]:
        parser.error("The frozen profile still has unresolved rule/evidence conflicts")
    if args.output is None:
        parser.error("--execute requires an explicit new --output under BROWSER_EVAL_DATA_HOME")
    output = args.output.expanduser().resolve()
    allowed = data_home().resolve()
    if output == allowed or not output.is_relative_to(allowed):
        parser.error("Measured output must be a new directory below BROWSER_EVAL_DATA_HOME")
    if output.exists():
        parser.error("Output exists; measured evidence is immutable")
    for key in PROXY_VARIABLES:
        os.environ.pop(key, None)
    os.environ["LANG"] = "C"
    os.environ["LC_ALL"] = "C"
    profile_sha256 = _digest(profile_path)
    measured_dataset = measured_risk_control_dataset(profile, dataset, profile_sha256)
    execution = profile["execution"]
    proof_before = frozen_risk_control_input_snapshot(profile_path)
    pre_identity = implementation_identity(provider)

    from harness.web_fetch.runner import run_evaluation

    records, _ = run_evaluation(
        output_dir=output,
        dataset_meta=measured_dataset,
        dataset_path=rooted(profile["dataset"]["path"]),
        cases=cases,
        providers=[provider],
        provider_config_path=config_path,
        runs=execution["runs_per_case"],
        timeout=execution["timeout_seconds"],
        delay=execution["delay_seconds"],
        provider_concurrency=execution["concurrency"],
        max_failure_retries=execution["max_failure_retries"],
    )
    if len(records) != 100 or len({row["case_id"] for row in records}) != 100:
        raise ValueError("Incomplete risk-control Moli matrix")
    require_complete_diagnostic_logs(records)
    post_identity = implementation_identity(provider, refresh=True)
    if post_identity != pre_identity:
        raise ValueError("Moli implementation identity changed during execution")
    proof_after = frozen_risk_control_input_snapshot(profile_path)
    if proof_after != proof_before:
        raise ValueError("Frozen benchmark inputs changed during execution")
    proof_verification_path = output / "frozen-input-verification.json"
    proof_verification_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "risk_control_frozen_input_verification",
                "before": proof_before,
                "after": proof_after,
                "unchanged": True,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    proof_verification_sha256 = _digest(proof_verification_path)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    manifest["run_profile_sha256"] = profile_sha256
    manifest["frozen_input_verification_sha256"] = proof_verification_sha256
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "execution.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "aggregate": {
                    "providers": [profile["provider"]["name"]],
                    "case_ids": [case.id for case in cases],
                    "started_at": manifest["started_at"],
                    "completed_at": datetime.now(UTC).isoformat(),
                    "batch_count": 1,
                },
                "batches": [manifest],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    results_path = output / "results.jsonl"
    certification = {
        "schema_version": 1,
        "kind": "certified_risk_control_100_moli_v2",
        "profile_sha256": profile_sha256,
        "dataset_sha256": _digest(output / "dataset.json"),
        "results_sha256": _digest(results_path),
        "manifest_sha256": _digest(output / "manifest.json"),
        "execution_sha256": _digest(output / "execution.json"),
        "frozen_input_verification_sha256": proof_verification_sha256,
        "provider_identity_before": pre_identity,
        "provider_identity_after": post_identity,
        "logical_results": len(records),
        "physical_calls": len(records),
        "complete_diagnostic_logs": True,
    }
    (output / "certification.json").write_text(
        json.dumps(certification, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    result = {"evidence": str(output), "logical_results": len(records)}
    if args.no_report:
        result["report_status"] = "not_built"
    else:
        from harness.web_fetch.report_build import build_report

        result["report"] = str(build_report(output, output / "report"))
    print(json.dumps(result, ensure_ascii=False, indent=2))
