import base64
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import pytest

from harness.web_fetch.artifacts import materialize_bound_artifacts
from harness.web_fetch.comparison_audit import audit_fixed_native_comparison
from harness.web_fetch.evidence import finalize_aggregate
from lexbench_webfetch.models import EvalCase
from harness.web_fetch.models import ProviderResult
from harness.web_fetch.providers.base import Provider
from harness.web_fetch.runner import run_evaluation


class NativeFixture(Provider):
    def __init__(self, name, config):
        super().__init__(name, config)
        self._implementation_identity = {
            "reported_version": config["expected_reported_version"],
            "binary_sha256": config["expected_binary_sha256"],
        }

    def fetch(self, case, timeout):
        diagnostic = (self.name + "/" + case.id + " debug\n").encode()
        metadata = {
            "route": "dynamic_native_markdown" if case.track == "dynamic" else "ordinary_markdown",
            "output": "markdown",
            "dump": "markdown",
            "diagnostic_log_sha256": hashlib.sha256(diagnostic).hexdigest(),
            "diagnostic_log_bytes": len(diagnostic),
            "diagnostic_log_truncated": False,
            "_diagnostic_log_b64": base64.b64encode(diagnostic).decode(),
        }
        if case.track == "dynamic":
            metadata.update(
                layout=True,
                with_frames=True,
                wait_until="domstable",
                wait={
                    "wait_until": "domstable",
                    "scope": "dynamic_task_family",
                    "strategy": "native_task_family_completion",
                    "layout": {
                        "requested": True,
                        "support": "native",
                        "interface": "--layout",
                        "scope": "dynamic_task_family",
                    },
                    "subdocuments": {
                        "requested": True,
                        "support": "native",
                        "interface": "--with-frames",
                        "harness_content_assembly": False,
                        "scope": "dynamic_task_family",
                    },
                },
            )
        if self.name == "moli-stack-1432" and case.id == "timeout":
            return ProviderResult(False, "", error_type="timeout", error_message="deadline",
                                  metadata=metadata)
        content = (
            "missing"
            if self.name == "moli-stack-1432" and case.id == "web"
            else "anchor"
        )
        return ProviderResult(True, content, metadata={**metadata,
            "reported_version": self.config["expected_reported_version"],
            "binary_sha256": self.config["expected_binary_sha256"],
        })


def comparison_cohort(tmp_path: Path, *, call_order="case-alternating") -> dict[str, Path]:
    source_cases = [
        EvalCase("web", "Web", "https://93.184.216.34/web", "web", ["anchor"]),
        EvalCase("dynamic", "Dynamic", "https://93.184.216.34/dynamic", "dynamic",
                 ["anchor"], track="dynamic"),
        EvalCase("timeout", "Timeout", "https://93.184.216.34/timeout", "web", ["anchor"]),
    ]
    source = {
        "id": "source",
        "tool": "web_fetch",
        "cases": [asdict(case) for case in source_cases],
    }
    source_path = tmp_path / "source.json"
    source_path.write_text(json.dumps(source))
    source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    measured = json.loads(json.dumps(source))
    measured["cases"][1]["retrieval_hints"] = {"version": 3, "mode": "native"}
    measured["comparison"] = {
        "input_dataset_sha256": source_hash,
        "retrieval_mode": "native_task_family_completion",
    }
    measured["coverage_plan"] = {
        "web": ["moli", "moli-stack-1432"],
        "dynamic": ["moli", "moli-stack-1432"],
    }
    measured_path = tmp_path / "measured.json"
    measured_path.write_text(json.dumps(measured))

    configs = {
        name: {
            "type": "moli_webfetch",
            "label": name,
            "binary": "/fixture/" + name,
            "expected_reported_version": "moli " + ("1.1.15" if name == "moli" else "stack"),
            "expected_binary_sha256": character * 64,
            "dns_mode": "system",
            "wait_until": "done",
            "dynamic_wait_until": "domstable",
            "block_private_networks": True,
            "uniform_native_parameters": True,
            "max_bytes": 20_000_000,
            "log_level": "debug",
            "retain_complete_diagnostic_log": True,
            "max_diagnostic_bytes": 100_000_000,
            **({"source_url": "https://example.test/candidate"}
               if name == "moli-stack-1432" else {}),
        }
        for name, character in (("moli", "a"), ("moli-stack-1432", "b"))
    }
    config_path = tmp_path / "providers.json"
    config_path.write_text(json.dumps({"schema_version": 1, "providers": configs}))
    evidence = tmp_path / "evidence"
    worker = evidence / "workers" / "interleaved"
    providers = [
        NativeFixture(name, configs[name]) for name in ("moli", "moli-stack-1432")
    ]
    records, _ = run_evaluation(
        output_dir=worker,
        dataset_meta=measured,
        dataset_path=measured_path,
        cases=[EvalCase(**case) for case in measured["cases"]],
        providers=providers,
        provider_config_path=config_path,
        runs=1,
        timeout=1,
        delay=0,
        max_failure_retries=0,
        call_order=call_order,
    )
    materialize_bound_artifacts(evidence, worker, records)
    manifest = json.loads((worker / "manifest.json").read_text())
    (evidence / "dataset.json").write_text(json.dumps(measured))
    (evidence / "results.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in records)
    )
    (evidence / "execution.json").write_text(json.dumps({
        "schema_version": 1,
        "aggregate": {
            "providers": ["moli", "moli-stack-1432"],
            "case_ids": ["web", "dynamic", "timeout"],
            "batch_count": 1,
            "call_order": call_order,
        },
        "batches": [manifest],
    }))
    observations = tmp_path / "observations.json"
    observations.write_text("[]\n")
    finalize_aggregate(evidence, observations)

    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps({
        "schema_version": 1,
        "inputs": {"dataset_sha256": source_hash, "case_count": 3},
        "stable": {
            "reported_version": configs["moli"]["expected_reported_version"],
            "binary_sha256": configs["moli"]["expected_binary_sha256"],
            "source_revision": "1" * 40,
        },
        "candidate": {
            "reported_version": configs["moli-stack-1432"]["expected_reported_version"],
            "binary_sha256": configs["moli-stack-1432"]["expected_binary_sha256"],
            "revision": "2" * 40,
        },
        "execution": {
            "workers": 1,
            "call_order": call_order,
            "runs": 1,
            "caller_retries": 0,
            "retrieval_mode": "native",
            "runtime_source_sha256": manifest["runtime_source_sha256"],
            "evidence_state": "certified",
        },
    }))
    return {
        "evidence": evidence,
        "source": source_path,
        "config": config_path,
        "receipt": receipt_path,
        "json": tmp_path / "audit.json",
        "markdown": tmp_path / "audit.md",
    }


def run_audit(paths):
    return audit_fixed_native_comparison(
        paths["evidence"],
        source_dataset=paths["source"],
        provider_config=paths["config"],
        run_receipt=paths["receipt"],
        output_json=paths["json"],
        output_markdown=paths["markdown"],
        stable_provider="moli",
        candidate_provider="moli-stack-1432",
    )


def convert_to_provider_batches(paths):
    config = json.loads(paths["config"].read_text())
    paths["config"].write_text(json.dumps(config))
    config_sha = hashlib.sha256(paths["config"].read_bytes()).hexdigest()

    rows = [json.loads(line) for line in
            paths["evidence"].joinpath("results.jsonl").read_text().splitlines()]
    providers = ["moli", "moli-stack-1432"]

    execution_path = paths["evidence"] / "execution.json"
    execution = json.loads(execution_path.read_text())
    original = execution["batches"][0]
    batches = []
    for provider in providers:
        batch = json.loads(json.dumps(original))
        batch["providers"] = [
            item for item in batch["providers"] if item["name"] == provider
        ]
        batch["parameters"].update(
            call_order="provider",
            concurrency_scope="providers_parallel_cases_sequential_per_provider",
        )
        batch["attempt_ids"] = [
            row["attempt_id"] for row in rows if row["provider"] == provider
        ]
        batch["attempt_count"] = len(batch["attempt_ids"])
        batch["provider_config_sha256"] = config_sha
        batches.append(batch)
    execution["aggregate"].update(batch_count=2, call_order="provider")
    execution["batches"] = batches
    execution_path.write_text(json.dumps(execution))

    receipt = json.loads(paths["receipt"].read_text())
    receipt["execution"]["call_order"] = "provider"
    paths["receipt"].write_text(json.dumps(receipt))


def test_comparison_audit_proves_protocol_and_lists_every_case(tmp_path):
    paths = comparison_cohort(tmp_path)
    audit = run_audit(paths)
    assert audit["scope"]["result_count"] == 6
    assert audit["verdict_relations"] == {
        "pass_to_pass": 1,
        "pass_to_fail": 2,
        "fail_to_pass": 0,
        "fail_to_fail": 0,
    }
    assert [row["case_id"] for row in audit["cases"]] == ["web", "dynamic", "timeout"]
    assert audit["cases"][0]["call_order"] == ["moli", "moli-stack-1432"]
    assert audit["cases"][1]["call_order"] == ["moli-stack-1432", "moli"]
    assert "`web`" in paths["markdown"].read_text()


def test_comparison_audit_accepts_serial_provider_batches_with_native_defaults(tmp_path):
    paths = comparison_cohort(tmp_path, call_order="provider")
    convert_to_provider_batches(paths)
    audit = run_audit(paths)
    assert audit["scope"]["call_order"] == "serial_provider_batches"
    assert all(row["call_order"] == ["moli", "moli-stack-1432"]
               for row in audit["cases"])
    assert "serial provider-batch order" in paths["markdown"].read_text()


@pytest.mark.parametrize("value", [None, False])
def test_comparison_audit_requires_uniform_native_parameters(tmp_path, value):
    paths = comparison_cohort(tmp_path)
    config = json.loads(paths["config"].read_text())
    for item in config["providers"].values():
        if value is None:
            item.pop("uniform_native_parameters")
        else:
            item["uniform_native_parameters"] = value
    paths["config"].write_text(json.dumps(config))
    with pytest.raises(ValueError, match="provider configuration is not fixed native"):
        run_audit(paths)


def test_comparison_audit_keeps_diagnostic_evidence_explicit(tmp_path):
    paths = comparison_cohort(tmp_path)
    dataset_path = paths["evidence"] / "dataset.json"
    dataset = json.loads(dataset_path.read_text())
    dataset.setdefault("comparison", {})["evidence_state"] = "diagnostic"
    dataset_path.write_text(json.dumps(dataset))
    execution_path = paths["evidence"] / "execution.json"
    execution = json.loads(execution_path.read_text())
    for batch in execution["batches"]:
        batch["evidence_state"] = "diagnostic"
    execution_path.write_text(json.dumps(execution))
    receipt = json.loads(paths["receipt"].read_text())
    receipt["execution"]["evidence_state"] = "diagnostic"
    paths["receipt"].write_text(json.dumps(receipt))

    audit = run_audit(paths)
    assert audit["scope"]["evidence_state"] == "diagnostic"


@pytest.mark.parametrize("damage", ["missing_result", "parameter_drift", "artifact", "order"])
def test_comparison_audit_rejects_incomplete_or_unfair_evidence(tmp_path, damage):
    paths = comparison_cohort(tmp_path)
    if damage == "missing_result":
        rows = paths["evidence"].joinpath("results.jsonl").read_text().splitlines()
        paths["evidence"].joinpath("results.jsonl").write_text("\n".join(rows[:-1]) + "\n")
    elif damage == "parameter_drift":
        config = json.loads(paths["config"].read_text())
        config["providers"]["moli-stack-1432"]["max_bytes"] = 10
        paths["config"].write_text(json.dumps(config))
    elif damage == "artifact":
        row = json.loads(paths["evidence"].joinpath("results.jsonl").read_text().splitlines()[0])
        paths["evidence"].joinpath(row["artifact_path"]).write_text("changed")
    else:
        rows = [json.loads(line) for line in
                paths["evidence"].joinpath("results.jsonl").read_text().splitlines()]
        rows[0]["started_at"], rows[-1]["started_at"] = (
            rows[-1]["started_at"], rows[0]["started_at"]
        )
        paths["evidence"].joinpath("results.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows)
        )
    with pytest.raises(ValueError):
        run_audit(paths)
