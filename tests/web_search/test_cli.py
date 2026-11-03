"""The one Web Search entry point reaches every owner through its public boundary."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from harness.reporting.templates import resolve_template
from harness.web_search.cli import COMMANDS, PROG, main
from harness.web_search.example import example

SOURCE = Path(__file__).resolve().parents[2] / "src"


def run_module(*arguments, cwd):
    # Checkout code wins over any installed distribution.
    path = os.pathsep.join(filter(None, [str(SOURCE), os.environ.get("PYTHONPATH")]))
    return subprocess.run([sys.executable, "-m", "harness.web_search", *arguments], cwd=cwd,
                          env={**os.environ, "PYTHONPATH": path}, text=True, capture_output=True)


def test_top_level_help_lists_every_command(tmp_path):
    result = run_module("--help", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith(f"usage: {PROG} ")
    listed = " ".join(result.stdout.split())
    for name, (_, _, summary) in COMMANDS.items():
        assert name in listed and summary in listed


@pytest.mark.parametrize("command, option", [
    ("report", "--release-check"),
    ("visual", "--receipt"),
    ("judge", "--reuse-evidence"),
    ("review", "{freeze,run,collect}"),
    ("publish", "--visual-receipt"),
])
def test_command_help_comes_from_its_owner_under_the_public_name(tmp_path, command, option):
    result = run_module(command, "--help", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith(f"usage: {PROG} {command} ")
    assert option in result.stdout


def test_review_keeps_its_nested_commands(tmp_path):
    result = run_module("review", "collect", "--help", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith(f"usage: {PROG} review collect ")
    assert "--require-complete" in result.stdout


@pytest.mark.parametrize("arguments", [[], ["unknown"]])
def test_missing_or_unknown_command_fails_without_running_an_owner(tmp_path, arguments):
    result = run_module(*arguments, cwd=tmp_path)
    assert result.returncode == 2
    assert f"usage: {PROG} " in result.stderr


def test_owner_rejects_an_unknown_option_the_router_passed_through(tmp_path):
    result = run_module("report", "--output", str(tmp_path), "--not-an-option", cwd=tmp_path)
    assert result.returncode == 2
    assert "--not-an-option" in result.stderr


def test_report_command_checks_a_generated_report_and_restores_process_arguments(
    tmp_path, capsys, monkeypatch
):
    example(tmp_path)
    monkeypatch.setattr(sys, "argv", ["caller"])
    main(["report", "--output", str(tmp_path / "report"), "--check"])
    assert "Web Search report checks passed" in capsys.readouterr().out
    assert sys.argv == ["caller"]


def test_template_registry_names_the_entry_point_and_public_commands():
    template = resolve_template("web-search")
    assert template["entrypoint"] == "src/harness/web_search/cli.py"
    for key in ("build", "check", "release_check"):
        assert template[key].startswith("uv run --frozen python -m harness.web_search report ")
