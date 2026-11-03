"""The one Lexbench entry point reaches every owner through its public boundary."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from harness.lexbench.cli import COMMANDS, PROG, main
from harness.lexbench.report import TEMPLATE_DIRECTORY, TEMPLATE_FILES

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"
# A distinctive option each owner declares, so the help cannot come from the router.
OWNER_OPTIONS = {
    "report": "--linux-resource-profiles",
    "check": "--release",
    "package": "--source",
    "reproduce": "--agent-browser",
    "resource-profiles": "--protocol",
    "resource-matrix": "--protocol",
    "resource-summary": "--directory",
    "chart": "--metrics",
}


def checkout_env():
    """Run `-m harness.lexbench` from this checkout, whatever install the interpreter holds."""
    path = os.pathsep.join(filter(None, [str(SOURCE), os.environ.get("PYTHONPATH")]))
    return {**os.environ, "PYTHONPATH": path}


def run_module(*arguments, cwd):
    return subprocess.run([sys.executable, "-m", "harness.lexbench", *arguments], cwd=cwd,
                          env=checkout_env(), text=True, capture_output=True)


def test_every_command_declares_a_distinctive_owner_option():
    assert set(OWNER_OPTIONS) == set(COMMANDS)


def test_top_level_help_lists_every_command(tmp_path):
    result = run_module("--help", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith(f"usage: {PROG} ")
    listed = " ".join(result.stdout.split())
    for name, (_, summary) in COMMANDS.items():
        assert name in listed and summary in listed


@pytest.mark.parametrize("command, option", OWNER_OPTIONS.items())
def test_command_help_comes_from_its_owner_under_the_public_name(tmp_path, command, option):
    result = run_module(command, "--help", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith(f"usage: {PROG} {command} ")
    assert option in result.stdout


@pytest.mark.parametrize("arguments", [[], ["unknown"]])
def test_missing_or_unknown_command_fails_without_running_an_owner(tmp_path, arguments):
    result = run_module(*arguments, cwd=tmp_path)
    assert result.returncode == 2
    assert f"usage: {PROG} " in result.stderr


def test_router_restores_the_caller_arguments(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["caller", "--unrelated"])
    with pytest.raises(SystemExit):
        main(["check", "--help"])
    assert sys.argv == ["caller", "--unrelated"]


def test_guide_shows_each_operator_command():
    guide = (ROOT / "src/harness/lexbench/README.md").read_text(encoding="utf-8")
    inventory = (ROOT / "scripts/README.md").read_text(encoding="utf-8")
    for command in COMMANDS:
        assert f"harness.lexbench {command}" in guide, command
    assert "harness.lexbench" in inventory and "moli_report" not in inventory


def test_maintained_template_supplies_every_file_the_report_copies():
    assert all((TEMPLATE_DIRECTORY / name).is_file() for name in TEMPLATE_FILES)
    assert not (ROOT / "scripts/moli_report").exists()
