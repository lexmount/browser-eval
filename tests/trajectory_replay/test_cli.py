"""The curated Trajectory Replay entry point reaches every owner through its public boundary."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from harness.reporting.templates import resolve_template, template_identity
from harness.trajectory_replay import webarena_report_check as checker
from harness.trajectory_replay.cli import COMMANDS, PROG, main

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src"


def run_module(*arguments, cwd):
    path = os.pathsep.join(filter(None, [str(SOURCE), os.environ.get("PYTHONPATH")]))
    return subprocess.run([sys.executable, "-m", "harness.trajectory_replay", *arguments], cwd=cwd,
                          env={**os.environ, "PYTHONPATH": path}, text=True, capture_output=True)


def test_top_level_help_lists_every_command(tmp_path):
    result = run_module("--help", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith(f"usage: {PROG} ")
    listed = " ".join(result.stdout.split())
    for name, (_, _, summary) in COMMANDS.items():
        assert name in listed and summary in listed


def test_top_level_help_does_not_load_owners(tmp_path):
    probe = ("import sys; from harness.trajectory_replay.cli import main\n"
             "try:\n    main(['--help'])\nexcept SystemExit:\n    pass\n"
             "assert 'harness.trajectory_replay.webarena_report' not in sys.modules\n"
             "assert 'harness.trajectory_replay.runner' not in sys.modules\n")
    path = os.pathsep.join(filter(None, [str(SOURCE), os.environ.get("PYTHONPATH")]))
    result = subprocess.run([sys.executable, "-c", probe], cwd=tmp_path, text=True,
                            capture_output=True, env={**os.environ, "PYTHONPATH": path})
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("command", sorted(COMMANDS))
def test_every_command_reaches_its_owner_help(tmp_path, command):
    pytest.importorskip("webarena_verified")
    result = run_module(command, "--help", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert " ".join(result.stdout.split()).startswith(f"usage: {PROG} {command} ")


@pytest.mark.parametrize("command, option", [
    ("evaluate", "--ground-truth-evidence"),
    ("replay", "--qualified-dataset"),
    ("seeds", "--evidence"),
    ("expert-seeds", "--repairs"),
    ("promote", "--receipt"),
    ("catalog-report", "--portable-suite"),
    ("audit-readiness", "--results"),
    ("dataset-revision", "--corrections"),
    ("check", "--profile"),
    ("visual", "--receipt"),
])
def test_command_help_comes_from_its_owner_under_the_public_name(tmp_path, command, option):
    pytest.importorskip("webarena_verified")
    result = run_module(command, "--help", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert " ".join(result.stdout.split()).startswith(f"usage: {PROG} {command} ")
    assert option in result.stdout


@pytest.mark.parametrize("arguments", [[], ["unknown"]])
def test_missing_or_unknown_command_fails_without_running_an_owner(tmp_path, arguments):
    result = run_module(*arguments, cwd=tmp_path)
    assert result.returncode == 2
    assert f"usage: {PROG} " in result.stderr


def test_owner_rejects_an_unknown_option_the_router_passed_through(tmp_path):
    result = run_module("audit-readiness", "--results", "a", "--output", "b", "--not-an-option",
                        cwd=tmp_path)
    assert result.returncode == 2
    assert "--not-an-option" in result.stderr


def test_positional_report_argument_reaches_the_owner(tmp_path):
    pytest.importorskip("webarena_verified")
    report = tmp_path / "source"
    report.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(report, target_is_directory=True)
    result = run_module("check", str(linked), cwd=tmp_path)
    assert result.returncode != 0
    assert "symlink" in result.stderr.lower()


def test_mutation_diagnostic_exit_status_reaches_the_process(monkeypatch):
    remote_execution = pytest.importorskip("harness.trajectory_replay.remote_execution")
    monkeypatch.setattr(remote_execution, "main_mutation_diagnostic", lambda: 3)
    with pytest.raises(SystemExit) as stopped:
        main(["mutation-diagnostic"])
    assert stopped.value.code == 3


def test_seeds_command_writes_the_owner_manifest_and_restores_process_arguments(
    tmp_path, monkeypatch
):
    from harness.trajectory_replay import browser_use_seeds

    manifest = {"counts": {"replay_ready_tasks": 1}, "tasks": []}
    monkeypatch.setattr(browser_use_seeds, "build_replay_seed_manifest",
                        lambda evidence, profile: {**manifest, "evidence": evidence})
    profile = tmp_path / "profile.json"
    profile.write_text("{}", encoding="utf-8")
    output = tmp_path / "nested/seeds.json"
    monkeypatch.setattr(sys, "argv", ["caller"])
    main(["seeds", "--profile", str(profile), "--evidence", "a", "--evidence", "b",
          "--output", str(output)])
    assert sys.argv == ["caller"]
    assert json.loads(output.read_text(encoding="utf-8"))["evidence"] == ["a", "b"]


def test_template_registry_names_the_entry_point_and_public_commands():
    template = resolve_template("trajectory-replay")
    assert template["entrypoint"] == "src/harness/trajectory_replay/cli.py"
    for key, command in (("build", "report"), ("check", "check"), ("package", "package")):
        assert template[key].startswith(
            f"uv run --frozen python -m harness.trajectory_replay {command} "
        )


def test_report_template_gate_binds_the_renderer_not_the_moved_entry_point():
    current = template_identity("trajectory-replay")
    checker.check_template_identity({})
    checker.check_template_identity({"profile_sha256": "bound", "template": current})
    # A report built before the move keeps the entry point it recorded.
    checker.check_template_identity({
        "profile_sha256": "bound",
        "template": {**current, "entrypoint": "scripts/build_webarena_replay_report.py"},
    })
    for template in (None, {**current, "renderer": "other.py"}, {**current, "track": "other"}):
        with pytest.raises(ValueError, match="bypasses the maintained replay template"):
            checker.check_template_identity({"profile_sha256": "bound", "template": template})
