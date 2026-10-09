from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from harness.trajectory_replay import cli, runner
from harness.trajectory_replay.suite import TaskSuite

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def wrapper_environment(tmp_path):
    binaries = tmp_path / "bin"
    binaries.mkdir()
    log = tmp_path / "calls"
    for name, body in {
        "docker": 'echo docker >> "$CALL_LOG"\nexit 0\n',
        "uv": (
            'printf "%s\\n" "$@" >> "$CALL_LOG"\n'
            'while [ "$#" -gt 0 ]; do\n'
            '  if [ "$1" = --output ]; then shift; mkdir "$1"; fi\n'
            '  shift\n'
            'done\nexit "${CALL_EXIT:-0}"\n'
        ),
    }.items():
        path = binaries / name
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(0o755)
    env = {
        **os.environ,
        "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
        "CALL_LOG": str(log),
        "BROWSER_EVAL_DATA_HOME": str(tmp_path / "data"),
        "BROWSER_USE_EVAL_BUILD_IMAGES": "0",
    }
    env.pop("BROWSER_USE_EVAL_OUTPUT_DIR", None)
    return env, log


@pytest.mark.parametrize("kind", ["directory", "file", "symlink", "dangling", "parent"])
def test_wrapper_preserves_existing_destination_before_external_work(
    tmp_path, wrapper_environment, kind
):
    env, log = wrapper_environment
    output = tmp_path / "evidence"
    original = tmp_path / "original"
    original.mkdir()
    artifact = original / "attempts.jsonl"
    artifact.write_bytes(b"original evidence\n")
    if kind == "directory":
        output.mkdir()
        (output / "attempts.jsonl").write_bytes(artifact.read_bytes())
    elif kind == "file":
        output.write_bytes(artifact.read_bytes())
    else:
        output.symlink_to(original if kind in {"symlink", "parent"} else tmp_path / "missing")
        if kind == "parent":
            output = output / "nested" / "new"
    env["BROWSER_USE_EVAL_OUTPUT_DIR"] = str(output)
    result = subprocess.run(
        ["bash", str(ROOT / "examples/trajectory_replay/run.sh")],
        env=env, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert not log.exists()
    assert artifact.read_bytes() == b"original evidence\n"
    if kind == "directory":
        assert (output / "attempts.jsonl").read_bytes() == artifact.read_bytes()
    elif kind == "file":
        assert output.read_bytes() == artifact.read_bytes()
    else:
        if kind == "parent":
            assert (tmp_path / "evidence").is_symlink()
            assert not (original / "nested").exists()
        else:
            assert output.is_symlink()


def test_wrapper_defaults_to_distinct_external_run_and_report_paths(
    tmp_path, wrapper_environment
):
    env, log = wrapper_environment
    for _ in range(2):
        result = subprocess.run(
            ["bash", str(ROOT / "examples/trajectory_replay/run.sh")],
            cwd=tmp_path, env=env, capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
    calls = log.read_text().splitlines()
    outputs = [Path(calls[i + 1]) for i, value in enumerate(calls) if value == "--output"]
    reports = [Path(calls[i + 1]) for i, value in enumerate(calls) if value == "--reports"]
    assert len(outputs) == len(set(outputs)) == 2
    for output, report in zip(outputs, reports, strict=True):
        assert output.is_relative_to(Path(env["BROWSER_EVAL_DATA_HOME"]))
        assert report == output / "report"
        assert output.is_dir()


def test_failed_wrapper_run_preserves_a_previous_run(tmp_path, wrapper_environment):
    env, log = wrapper_environment
    previous = tmp_path / "data" / "trajectory-replay" / "runs" / "previous"
    previous.mkdir(parents=True)
    artifact = previous / "attempts.jsonl"
    artifact.write_bytes(b"completed run\n")
    env["CALL_EXIT"] = "23"
    result = subprocess.run(
        ["bash", str(ROOT / "examples/trajectory_replay/run.sh")],
        env=env, capture_output=True, text=True,
    )
    assert result.returncode == 23
    assert artifact.read_bytes() == b"completed run\n"
    assert "Evidence directory:" in result.stdout
    assert "--artifacts" not in log.read_text().splitlines()


def test_wrapper_resolves_relative_output_from_callers_directory(tmp_path, wrapper_environment):
    env, log = wrapper_environment
    env["BROWSER_USE_EVAL_OUTPUT_DIR"] = "selected-run"
    result = subprocess.run(
        ["bash", str(ROOT / "examples/trajectory_replay/run.sh")],
        cwd=tmp_path, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "selected-run").is_dir()
    calls = log.read_text().splitlines()
    assert calls[calls.index("--output") + 1] == str(tmp_path / "selected-run")


@pytest.mark.parametrize("kind", ["directory", "file", "symlink", "dangling", "parent"])
def test_shared_runner_rejects_reused_or_linked_output_before_loading_inputs(
    tmp_path, monkeypatch, kind
):
    output = tmp_path / "evidence"
    original = tmp_path / "original"
    original.mkdir()
    artifact = original / "attempts.jsonl"
    artifact.write_bytes(b"immutable\n")
    if kind == "directory":
        output.mkdir()
    elif kind == "file":
        output.write_bytes(b"immutable\n")
    else:
        output.symlink_to(original if kind != "dangling" else tmp_path / "missing")
        if kind == "parent":
            output /= "new"

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid output must fail before input loading or browser setup")

    monkeypatch.setattr(runner, "load_suite", forbidden)
    monkeypatch.setattr(runner.docker, "from_env", forbidden)
    with pytest.raises(ValueError, match="[Oo]utput"):
        runner.run_evaluation(
            suite_path=tmp_path / "missing-suite.json", output_dir=output,
            reports_dir=tmp_path / "report",
        )
    assert artifact.read_bytes() == b"immutable\n"


@pytest.mark.parametrize("kind", ["occupied", "linked_parent"])
def test_runner_rechecks_destination_after_loading_inputs(tmp_path, monkeypatch, kind):
    parent = tmp_path / "parent"
    parent.mkdir()
    output = parent / "run"
    other = tmp_path / "other"
    other.mkdir()

    def load_suite(_):
        if kind == "occupied":
            output.mkdir()
            (output / "attempts.jsonl").write_bytes(b"competing run\n")
        else:
            parent.rmdir()
            parent.symlink_to(other)
        return TaskSuite("test", ())

    monkeypatch.setattr(runner, "load_suite", load_suite)
    monkeypatch.setattr(runner.docker, "from_env", lambda: pytest.fail("Browser setup reached"))
    with pytest.raises(ValueError, match="Output"):
        runner.run_evaluation(
            suite_path=tmp_path / "suite.json", output_dir=output,
            reports_dir=output / "report",
        )
    if kind == "occupied":
        assert (output / "attempts.jsonl").read_bytes() == b"competing run\n"
    else:
        assert not (other / "run").exists()


def test_direct_cli_requires_explicit_inputs_and_keeps_report_with_run(monkeypatch, tmp_path):
    import sys

    calls = []
    monkeypatch.setattr(cli, "run_evaluation", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(sys, "argv", ["replay"])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    assert calls == []
    output = tmp_path / "run"
    monkeypatch.setattr(sys, "argv", [
        "replay", "--suite", "suite.json", "--ground-truth-evidence", "qualification.json",
        "--output", str(output),
    ])
    cli.main()
    assert calls[0]["reports_dir"] == output / "report"
    assert calls[0]["suite_path"] == "suite.json"
    assert calls[0]["ground_truth_evidence_path"] == "qualification.json"
