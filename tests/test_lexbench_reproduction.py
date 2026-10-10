"""Frozen reproduction validates all inputs before preparing or executing a call."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from harness.lexbench import reproduction

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "scripts/moli_report/run.py"


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = value if isinstance(value, bytes) else json.dumps(value).encode()
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


@pytest.fixture
def frozen(tmp_path):
    repo, modules, agent = tmp_path / "runner", tmp_path / "modules", tmp_path / "agent"
    tasks = [f"task-{i}" for i in range(372)]
    inputs, records, definitions = {}, [], {}
    for task in tasks:
        relative = f"tasks/{task}.json"
        digest = write(repo / relative, {"id": task})
        inputs[relative] = digest
        records.append({"task_id": task, "path": relative, "sha256": digest})
        definitions[task] = {"path": relative, "sha256": digest}
    inputs["runner/run.py"] = write(repo / "runner/run.py", b"# synthetic driver\n")
    cohort = {
        "schema": "moli_historical_failures_resource/1",
        "ordered_task_ids": tasks,
        "strata": {"synthetic": records},
        "k_runs": 3,
        "jobs": 1,
        "seed": "fixed-seed",
        "try_layout": False,
        "resource_sample_interval_ms": 500,
        "chrome_baseline": "off",
        "score_mode": "independent",
        "manifest_sha256": write(repo / "manifest.json", {"tasks": tasks}),
        "moli_sha256": write(repo / "build_artifacts/moli/bin/moli", b"synthetic browser"),
    }
    protocol = {
        "schema": "moli_011_failure_retest/1",
        "task_ids": tasks,
        "tasks": definitions,
        "k": 3,
        "jobs": 1,
        "seed": "fixed-seed",
        "try_layout": False,
        "sample_interval_ms": 500,
        "chrome_baseline": "off",
        "score_mode": "independent",
        "image": "sha256:" + "a" * 64,
        "inputs": inputs,
        "node_modules_lock": write(modules / ".package-lock.json", {"fixture": True}),
        "agent_browser_sha256": write(agent, b"synthetic agent-browser"),
    }
    cohort_path, protocol_path = tmp_path / "cohort.json", tmp_path / "protocol.json"
    write(cohort_path, cohort)
    write(protocol_path, protocol)
    return dict(
        repo=repo,
        out=tmp_path / "output",
        image=protocol["image"],
        modules=modules,
        agent=agent,
        cohort=cohort_path,
        protocol=protocol_path,
    )


def arguments(frozen, mode="off", phase="baseline"):
    flags = {
        "repo": "--repo",
        "out": "--output",
        "image": "--image",
        "modules": "--node-modules",
        "agent": "--agent-browser",
        "cohort": "--cohort",
        "protocol": "--protocol",
    }
    return [piece for key, flag in flags.items() for piece in (flag, str(frozen[key]))] + [
        "--mode",
        mode,
        "--phase",
        phase,
    ]


@pytest.mark.parametrize(
    "mode,phase", [("off", "baseline"), ("on", "baseline"), ("on", "engine"), ("off", "engine")]
)
def test_complete_frozen_scope_prepares_readonly_serial_command_without_execution(
    frozen, mode, phase
):
    command = reproduction.command(**frozen, mode=mode, phase=phase)
    assert command[:4] == ["docker", "run", "--rm", "--init"]
    assert command[command.index("--moli-layout") + 1] == mode
    assert command[command.index("--resource-profile") + 1] == phase
    assert command[command.index("--k") + 1] == "3"
    assert command[command.index("--jobs") + 1] == "1"
    assert command[command.index("--run-id") + 1] == f"{mode}_{phase}"
    assert command[command.index("--resource-sample-interval-ms") + 1] == "500"
    assert [command[i + 1] for i, value in enumerate(command) if value == "--task"] == [
        f"task-{i}" for i in range(372)
    ]
    assert "--try-layout" not in command
    assert ("--resource-calibration-baseline" in command) == (phase == "engine")
    for root in ("repo", "modules", "agent"):
        assert any(
            f"src={frozen[root].resolve()}," in arg and arg.endswith(",readonly") for arg in command
        )
    assert not frozen["out"].exists()


@pytest.mark.parametrize("optimized", [False, True])
def test_changed_binary_is_rejected_under_both_python_modes(frozen, optimized, tmp_path):
    (frozen["repo"] / "build_artifacts/moli/bin/moli").write_bytes(b"wrong build")
    command = [sys.executable] + (["-O"] if optimized else []) + [str(ENTRY), *arguments(frozen)]
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode != 0
    assert "Frozen input changed: moli" in result.stderr
    assert not frozen["out"].exists()


def test_published_reproduction_source_uses_adjacent_frozen_bindings(frozen, tmp_path):
    published = tmp_path / "run.py"
    published.write_bytes(Path(reproduction.__file__).read_bytes())
    args = arguments(frozen)
    for flag in ("--cohort", "--protocol"):
        index = args.index(flag)
        del args[index : index + 2]
    result = subprocess.run(
        [sys.executable, str(published), *args],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout) == reproduction.command(**frozen, mode="off", phase="baseline")
    assert not frozen["out"].exists()


@pytest.mark.parametrize(
    "field,value", [("jobs", 2), ("try_layout", True), ("k", 1), ("seed", "other")]
)
def test_changed_experiment_contract_is_rejected(frozen, field, value):
    protocol = json.loads(frozen["protocol"].read_text())
    protocol[field] = value
    write(frozen["protocol"], protocol)
    with pytest.raises(ValueError, match="frozen layout cohort"):
        reproduction.command(**frozen, mode="off", phase="baseline")
    assert not frozen["out"].exists()


def test_parent_path_in_frozen_input_is_rejected_before_execution(frozen):
    protocol = json.loads(frozen["protocol"].read_text())
    protocol["inputs"]["../outside"] = "a" * 64
    write(frozen["protocol"], protocol)
    with pytest.raises(ValueError, match="relative"):
        reproduction.command(**frozen, mode="off", phase="baseline")


def test_input_change_during_execution_is_rejected(frozen, monkeypatch):
    def fake_execution(command, *, check):
        assert check
        (frozen["repo"] / "runner/run.py").write_bytes(b"changed driver")

    monkeypatch.setattr(reproduction.subprocess, "run", fake_execution)
    with pytest.raises(ValueError, match="Frozen input changed: run.py"):
        reproduction.main([*arguments(frozen), "--execute"])


def test_existing_execution_is_preserved(frozen, monkeypatch):
    previous = frozen["out"] / "off_baseline"
    previous.mkdir(parents=True)
    (previous / "kept").write_text("earlier evidence")
    monkeypatch.setattr(
        reproduction.subprocess, "run", lambda *a, **k: pytest.fail("execution started")
    )
    with pytest.raises(ValueError, match="Run already exists"):
        reproduction.main([*arguments(frozen), "--execute"])
    assert (previous / "kept").read_text() == "earlier evidence"


def test_command_import_and_help_have_no_private_input_dependency(tmp_path):
    code = (
        "import runpy,sys; sys.argv=['caller','--unrelated']; "
        f"runpy.run_path({str(ENTRY)!r},run_name='imported')"
    )
    subprocess.run([sys.executable, "-c", code], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        [sys.executable, str(ENTRY), "--help"], cwd=tmp_path, check=True, capture_output=True
    )
