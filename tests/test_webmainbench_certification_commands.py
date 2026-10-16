"""Certified entry points honor caller paths and reject drift before issuing a receipt."""

import json
import subprocess
from pathlib import Path

import pytest

from harness.web_fetch import webmainbench_certification as certification
from harness.web_fetch.webmainbench_scoring import CORE_METRICS, aggregate
from tests.webmainbench_case_fixture import certification_fixture, measured_shard, write_json, write_rows


@pytest.fixture
def command_inputs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return certification_fixture(tmp_path)


def arguments():
    return ["--source", "source.jsonl", "--scorer", "scorer", "--profile", "profile.json",
            "--output-root", "run", "--config", "providers.json", "--env-file", "credentials.env"]


def install_child(command_inputs, monkeypatch, *, after=None):
    root, profile, records = command_inputs
    original = subprocess.run
    commands = []

    def child(command, **kwargs):
        if "--provider" not in command:
            return original(command, **kwargs)
        commands.append(command)
        assert command[1:3] == ["-m", "harness.web_fetch.webmainbench_runner"]
        for option in ("--dataset", "--output", "--config", "--env-file"):
            if option != "--env-file" or option in command:
                assert Path(command[command.index(option) + 1]).is_absolute()
        assert kwargs.get("cwd", root) == root
        provider = command[command.index("--provider") + 1]
        measured_shard(Path(command[command.index("--output") + 1]), provider, records, profile)
        if after:
            after(root, provider)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", child)
    return commands


def test_declared_method_subset_and_caller_relative_paths(command_inputs, monkeypatch):
    root, profile, _ = command_inputs
    commands = install_child(command_inputs, monkeypatch)
    certification.main(arguments())
    receipt = json.loads((root / "run/cohort-certificate.json").read_text())
    assert [row["provider"] for row in receipt["providers"]] == profile["providers"]
    assert len(commands) == 2
    assert receipt["preflight"] == receipt["postflight"]


def test_default_profile_and_config_belong_to_caller_directory(command_inputs, monkeypatch):
    commands = install_child(command_inputs, monkeypatch)
    certification.main(["--source", "source.jsonl", "--scorer", "scorer", "--output-root", "run"])
    assert len(commands) == 2
    assert (command_inputs[0] / "run/cohort-certificate.json").is_file()


@pytest.mark.parametrize("damage", ["empty", "duplicate", "unknown", "version", "timeout", "retries"])
def test_invalid_profile_stops_before_any_child(command_inputs, monkeypatch, damage):
    root, profile, _ = command_inputs
    if damage == "empty":
        profile["providers"] = []
    elif damage == "duplicate":
        profile["providers"].append(profile["providers"][0])
    elif damage == "unknown":
        profile["providers"].append("unregistered")
    elif damage == "version":
        del profile["versions"]["obscura"]
    elif damage == "timeout":
        profile["timeout_seconds"] = 0
    else:
        profile["max_failure_retries"] = -1
    write_json(root / "profile.json", profile)
    commands = install_child(command_inputs, monkeypatch)
    with pytest.raises((SystemExit, ValueError)):
        certification.main(arguments())
    assert commands == []
    assert not (root / "run").exists()


def test_dirty_scorer_is_rejected_before_calls(command_inputs, monkeypatch):
    root, *_ = command_inputs
    (root / "scorer/webmainbench/metrics/metric.py").write_text("different scorer")
    commands = install_child(command_inputs, monkeypatch)
    with pytest.raises(ValueError, match="clean"):
        certification.main(arguments())
    assert commands == []
    assert not (root / "run").exists()


@pytest.mark.parametrize("changed", ["profile", "config", "source"])
def test_input_drift_never_produces_a_certificate(command_inputs, monkeypatch, changed):
    def after(root, _provider):
        path = root / {"profile": "profile.json", "config": "providers.json", "source": "source.jsonl"}[changed]
        path.write_text(path.read_text() + " ")

    commands = install_child(command_inputs, monkeypatch, after=after)
    with pytest.raises(ValueError, match="changed"):
        certification.main(arguments())
    assert len(commands) == 1
    assert not (command_inputs[0] / "run/cohort-certificate.json").exists()


def test_earlier_method_drift_is_rejected_before_certification(command_inputs, monkeypatch):
    def after(root, provider):
        if provider == "obscura":
            (root / "run/chromium/artifacts/case-0.md").write_text("changed earlier method")

    commands = install_child(command_inputs, monkeypatch, after=after)
    with pytest.raises(ValueError, match="artifact mismatch"):
        certification.main(arguments())
    assert len(commands) == 2
    assert not (command_inputs[0] / "run/cohort-certificate.json").exists()


def install_scoring_child(command_inputs, monkeypatch, *, after=None):
    install_child(command_inputs, monkeypatch)
    certification.main(arguments())
    original = subprocess.run
    commands = []
    root, profile, _ = command_inputs

    def score(command, **kwargs):
        if "--evidence" not in command:
            return original(command, **kwargs)
        commands.append(command)
        assert command[1:3] == ["-m", "harness.web_fetch.webmainbench_scoring"]
        assert kwargs.get("cwd", root) == root
        for option in ("--evidence", "--dataset", "--scorer-repo"):
            assert Path(command[command.index(option) + 1]).is_absolute()
        shard = Path(command[command.index("--evidence") + 1])
        rows = certification.read_jsonl(shard / "results.jsonl")
        scored = [{**row, "metrics": {name: {"score": 0.5, "success": True} for name in CORE_METRICS}} for row in rows]
        write_rows(shard / "scored-results.jsonl", scored)
        write_json(shard / "score-summary.json", {
            "provider": shard.name, "case_count": len(rows), "official_aggregate": aggregate(scored),
            "scorer": {"commit": profile["official_scorer_commit"], "metric_config": profile["metric_config"],
                       "metric_source_sha256": certification.scorer_source_binding(root / "scorer")}})
        if after:
            after(root, shard.name)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", score)
    return commands


def score_arguments():
    return ["--cohort", "run", "--source", "source.jsonl", "--scorer", "scorer", "--profile", "profile.json"]


def test_scoring_honors_paths_and_preserves_measurement_certificate(command_inputs, monkeypatch):
    commands = install_scoring_child(command_inputs, monkeypatch)
    root = command_inputs[0]
    before = (root / "run/cohort-certificate.json").read_bytes()
    certification.main_score(score_arguments())
    receipt = json.loads((root / "run/scoring-certificate.json").read_text())
    assert len(commands) == 2
    assert (root / "run/cohort-certificate.json").read_bytes() == before
    assert [r["provider"] for r in receipt["providers"]] == command_inputs[1]["providers"]


@pytest.mark.parametrize("damage", ["output", "cohort", "methods", "profile", "metadata-link", "log-link"])
def test_invalid_certified_inputs_stop_before_scoring(command_inputs, monkeypatch, damage):
    commands = install_scoring_child(command_inputs, monkeypatch)
    root = command_inputs[0]
    if damage == "output":
        (root / "run/chromium/artifacts/case-0.md").write_text("changed output")
    elif damage in ("cohort", "methods"):
        path = root / "run/cohort-certificate.json"
        value = json.loads(path.read_text())
        if damage == "cohort":
            value["preflight"]["case_count"] = 1
        else:
            value["providers"].pop()
        write_json(path, value)
    elif damage == "profile":
        path = root / "profile.json"
        path.write_text(path.read_text() + " ")
    else:
        target = root / "external.json"
        target.write_bytes((root / "run/chromium/dataset.json").read_bytes())
        path = root / "run/chromium" / ("dataset.json" if damage == "metadata-link" else "scoring.log")
        path.unlink(missing_ok=True)
        path.symlink_to(target)
    with pytest.raises((SystemExit, ValueError)):
        certification.main_score(score_arguments())
    assert commands == []
    assert not (root / "run/scoring-certificate.json").exists()


@pytest.mark.parametrize("changed", ["profile", "cohort", "earlier-method"])
def test_drift_during_scoring_never_receives_a_certificate(command_inputs, monkeypatch, changed):
    def after(root, _provider):
        path = root / {"profile": "profile.json", "cohort": "run/cohort-certificate.json",
                       "earlier-method": "run/chromium/results.jsonl"}[changed]
        path.write_text(path.read_text() + " ")

    commands = install_scoring_child(command_inputs, monkeypatch, after=after)
    with pytest.raises(ValueError, match="changed|differs"):
        certification.main_score(score_arguments())
    assert len(commands) == 1
    assert not (command_inputs[0] / "run/scoring-certificate.json").exists()
