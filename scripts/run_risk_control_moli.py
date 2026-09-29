#!/usr/bin/env python3
"""Validate or execute the frozen 100-case risk-control Moli cohort."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from harness.web_fetch.config import load_dataset, load_provider_config
from harness.web_fetch.artifacts import diagnostic_log_binding
from harness.web_fetch.providers import build_provider
from harness.web_fetch.report_build import build_report
from harness.web_fetch.retrieval_hints import plan_for_case
from harness.web_fetch.runner import run_evaluation
from harness.web_fetch.storage import data_home


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILE = ROOT / "evaluation/risk-control/run-profile.json"
PROXY_VARIABLES = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rooted(value: str) -> Path:
    path = Path(os.path.expandvars(value)).expanduser()
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def case_ids_digest(case_ids: list[str]) -> str:
    return hashlib.sha256(("\n".join(case_ids) + "\n").encode()).hexdigest()


def implementation_identity(provider, *, refresh: bool = False) -> dict:
    if refresh or not isinstance(getattr(provider, "_implementation_identity", None), dict):
        if errors := provider.validate():
            raise ValueError("Moli identity/configuration error: " + ", ".join(errors))
    return dict(provider._implementation_identity)


def require_complete_diagnostic_logs(records: list[dict]) -> None:
    """Fail closed unless every logical call retained its complete raw stderr."""
    incomplete = [
        row["case_id"] for row in records
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
        if not path.is_file() or digest(path) != binding["sha256"]:
            raise ValueError("Frozen source binding changed: " + binding["path"])
    dataset_path = rooted(profile["dataset"]["path"])
    if digest(dataset_path) != profile["dataset"]["sha256"]:
        raise ValueError("Frozen risk-control dataset changed")
    dataset, cases = load_dataset(dataset_path)
    ids = [case.id for case in cases]
    if (
        len(cases) != profile["dataset"]["case_count"]
        or dataset.get("site_count") != profile["dataset"]["site_count"]
        or case_ids_digest(ids) != profile["dataset"]["case_ids_sha256"]
    ):
        raise ValueError("Frozen risk-control case inventory changed")
    if any(
        case.track != "risk_control"
        or case.task_kind != "body"
        or case.retrieval_hints
        or plan_for_case(case) is not None
        for case in cases
    ):
        raise ValueError("Every risk-control case must use the same ordinary Markdown route")
    execution = profile["execution"]
    if execution != {
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
        "max_diagnostic_bytes": 100000000,
        "trace_network": False,
        "diagnostic_log_policy": "retain_complete_raw_stderr_for_every_call_outside_git",
        "report_diagnostic_log_access": "local_private_file_link",
    }:
        raise ValueError("Risk-control execution parameters changed")
    config_path = rooted(profile["provider"]["config_path"])
    if digest(config_path) != profile["provider"]["config_sha256"]:
        raise ValueError("Frozen Moli provider configuration changed")
    config = load_provider_config(config_path)
    provider_name = profile["provider"]["name"]
    if list(config) != [provider_name]:
        raise ValueError("Risk-control provider configuration must contain one Moli method")
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
    return profile, dataset, cases, provider, config_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--execute", action="store_true",
                        help="Make the 100 provider calls; omit for a call-free validation")
    args = parser.parse_args()
    profile_path = args.profile.resolve()
    profile, dataset, cases, provider, config_path = validate(profile_path)
    if not args.execute:
        print(json.dumps({
            "status": profile["status"],
            "unresolved_case_ids": profile["unresolved_case_ids"],
            "cases": len(cases),
            "provider": implementation_identity(provider),
            "provider_calls": 0,
        }, ensure_ascii=False, indent=2))
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
    measured_dataset = {
        **dataset,
        "coverage_plan": {"risk_control": [profile["provider"]["name"]]},
        "comparison": {
            "kind": "risk_control_100_moli",
            "timeout_seconds": profile["execution"]["timeout_seconds"],
            "runs_per_case": 1,
            "concurrency": 1,
            "max_failure_retries": 0,
            "conversion": "Moli native Markdown; wait-until done; no per-case options",
            "require_independent_result_review": False,
        },
    }
    pre_identity = implementation_identity(provider)
    records, _ = run_evaluation(
        output_dir=output,
        dataset_meta=measured_dataset,
        dataset_path=rooted(profile["dataset"]["path"]),
        cases=cases,
        providers=[provider],
        provider_config_path=config_path,
        runs=1,
        timeout=45,
        delay=0.5,
        provider_concurrency=1,
        max_failure_retries=0,
    )
    if len(records) != 100 or len({row["case_id"] for row in records}) != 100:
        raise ValueError("Incomplete risk-control Moli matrix")
    require_complete_diagnostic_logs(records)
    post_identity = implementation_identity(provider, refresh=True)
    if post_identity != pre_identity:
        raise ValueError("Moli implementation identity changed during execution")
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    manifest["run_profile_sha256"] = digest(profile_path)
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "execution.json").write_text(json.dumps({
        "schema_version": 1,
        "aggregate": {
            "providers": [profile["provider"]["name"]],
            "case_ids": [case.id for case in cases],
            "started_at": manifest["started_at"],
            "completed_at": datetime.now(UTC).isoformat(),
            "batch_count": 1,
        },
        "batches": [manifest],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = build_report(output, output / "report")
    print(json.dumps({
        "evidence": str(output),
        "logical_results": len(records),
        "report": str(report),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
