"""Saved resource evidence is data, not an executable report dependency."""

import hashlib
import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from harness.lexbench.resource_summary import summarize, validate_summary
from tests.test_resource_calibration import experiment


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def saved_profiles(tmp_path):
    runner = tmp_path / "historical-inputs"
    (runner / "runner").mkdir(parents=True)
    marker = tmp_path / "executed"
    poison = f'from pathlib import Path\nPath({str(marker)!r}).write_text("executed")\nraise RuntimeError("Evidence source was executed")\n'
    for filename in ("__init__.py", "resources.py"):
        (runner / "runner" / filename).write_text(poison)
    directory = tmp_path / "profiles"
    directory.mkdir()
    (directory / "orchestrator.py").write_text("# frozen synthetic collector\n")
    tasks = [f"task-{i}" for i in range(557)]
    configs = {
        f"build_{i}": {
            "sha256": str(i + 1) * 64,
            "layout": "on" if i % 2 else "off",
            "profile": "release",
            "target": "aarch64-unknown-linux-gnu",
        }
        for i in range(4)
    }
    protocol = {
        "schema": "moli_linux_resource_profiles/1",
        "memory_metric": "process_tree_pss",
        "attempts": 5,
        "jobs": 1,
        "task_ids": tasks,
        "configs": configs,
        "max_observer_effect_pct": 10,
        "runner_root": str(runner),
        "orchestrator_sha256": hashlib.sha256(
            (directory / "orchestrator.py").read_bytes()
        ).hexdigest(),
        "input_files": {
            f"runner/{name}": hashlib.sha256((runner / "runner" / name).read_bytes()).hexdigest()
            for name in ("__init__.py", "resources.py")
        },
        "chart_keys": [
            {"task_id": task, "attempt": attempt} for task in tasks[:209] for attempt in range(1, 6)
        ],
    }
    protocol_sha = write_json(directory / "protocol.json", protocol)
    receipt = {
        "schema": "moli_linux_resource_profiles_receipt/1",
        "protocol_sha256": protocol_sha,
        "orchestrator_sha256": protocol["orchestrator_sha256"],
        "results": {},
    }
    for method, config in configs.items():
        measured, baseline, after, before = experiment(count=557)
        for phase, samples, manifest in [
            ("baseline", baseline, before),
            ("engine", measured, after),
        ]:
            manifest.update(completion_status="completed", k_runs=5)
            manifest["engines"] = {"first": {"sha256": config["sha256"]}}
            manifest["resource_profile"]["mode"] = phase
            run = directory / f"{method}_{phase}"
            run.mkdir()
            rows = []
            for attempt in range(1, 6):
                for sample in samples:
                    resource = {
                        "collection_wall_ms": 108,
                        "pss_peak_bytes": 1024**2,
                        "cpu_total_ms": 10,
                        "samples_seen": 3,
                        "quality_flags": [],
                        "sample_interval_ms": 250,
                        "measurement_backend": {
                            "memory_pss": "proc_smaps_rollup",
                            "cpu": "proc_tree",
                        },
                    }
                    rows.append(
                        dict(
                            sample,
                            attempt=attempt,
                            resource=resource,
                            engine_provenance={
                                "binary_sha256": config["sha256"],
                                "layout_enabled": config["layout"] == "on",
                                "launch_command": ["fixture"]
                                + (["--layout"] if config["layout"] == "on" else []),
                            },
                        )
                    )
            (run / "results.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
            receipt["results"][f"{method}:{phase}"] = {
                "run_id": run.name,
                "rows": len(rows),
                "run_manifest_sha256": write_json(run / "run_manifest.json", manifest),
                "results_sha256": hashlib.sha256((run / "results.jsonl").read_bytes()).hexdigest(),
                "host_summary_sha256": write_json(run / "host_summary.json", {"polluted": False}),
            }
    write_json(directory / "receipt.json", receipt)
    return directory, configs, marker


def test_resource_summary_rebuilds_full_scope_without_executing_bound_python_inputs(tmp_path):
    directory, builds, marker = saved_profiles(tmp_path)
    result = summarize(directory)
    validate_summary(result, builds)
    assert not marker.exists()
    assert result["publishable"]
    assert all(
        configuration["calibration"]["matched_attempts"] == 2785
        for configuration in result["configurations"].values()
    )


def test_changed_source_provenance_is_rejected_without_executing_it(tmp_path):
    directory, _, marker = saved_profiles(tmp_path)
    protocol = json.loads((directory / "protocol.json").read_bytes())
    (Path(protocol["runner_root"]) / "runner/resources.py").write_text("changed")
    with pytest.raises(ValueError, match="runner changed"):
        summarize(directory)
    assert not marker.exists()


def test_report_builder_import_and_help_do_not_require_private_evidence(monkeypatch, tmp_path):
    monkeypatch.setenv("BROWSER_EVAL_DATA_HOME", str(tmp_path / "absent-data"))
    monkeypatch.setenv("LEXBENCH_REPORT_RUNNER_ROOT", str(tmp_path / "absent-runner"))
    module = importlib.import_module("harness.lexbench.report")
    with pytest.raises(SystemExit) as exit_status:
        module.main(["--help"])
    assert exit_status.value.code == 0
    command = Path(__file__).resolve().parents[1] / "scripts/moli_report/build.py"
    result = subprocess.run(
        [sys.executable, str(command), "--help"], text=True, capture_output=True
    )
    assert result.returncode == 0, result.stderr
    assert "--linux-resource-profiles" in result.stdout
    assert not (tmp_path / "absent-data").exists()


@pytest.mark.parametrize("optimized", [False, True])
@pytest.mark.parametrize("damage", ["protocol-hash", "population"])
def test_report_rejects_changed_protocol_and_partial_scope_before_output(
    tmp_path, optimized, damage
):
    root = Path(__file__).resolve().parents[1]
    evidence = tmp_path / "explicit-evidence"
    digest = "a" * 64
    write_json(
        evidence / "report-cohort.json",
        {"k_runs": 3, "strata": {"fixture": [{"task_id": "one", "sha256": digest}]}},
    )
    write_json(evidence / "report-runs.json", {})
    protocol_sha = write_json(
        evidence / "protocol.json",
        {"task_ids": ["one"], "tasks": {"one": {"sha256": digest}}, "try_layout": False},
    )
    write_json(
        evidence / "certification.json",
        {
            "protocol_sha256": "0" * 64 if damage == "protocol-hash" else protocol_sha,
            "artifact_sha256": {},
        },
    )
    output = tmp_path / "report"
    result = subprocess.run(
        [
            sys.executable,
            *(["-O"] if optimized else []),
            str(root / "scripts/moli_report/build.py"),
            "--cohort",
            str(evidence / "report-cohort.json"),
            "--output",
            str(output),
        ],
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert (
        "Protocol hash mismatch"
        if damage == "protocol-hash"
        else "Functional population differs from the report profile"
    ) in result.stderr
    assert not output.exists()
