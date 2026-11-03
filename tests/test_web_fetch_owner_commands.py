"""WCXB and WebMainBench stages reach their owners through the one Web Fetch entry point."""
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness.distribution import repository_root
from harness.reporting.templates import resolve_template
from harness.web_fetch import webmainbench_latency_correction as latency
from harness.web_fetch.cli import OWNER_COMMANDS, build_parser, main

ROOT = Path(__file__).resolve().parents[1]
PROG = build_parser().prog
DOCUMENTED_COMMAND_FILES = ("Makefile", "docs/benchmarks.md", "docs/architecture.md",
                            "src/harness/web_fetch/README.md", "src/harness/web_fetch/WEBMAINBENCH.md")
# A distinctive option each owner declares, so the help cannot come from the router.
OWNER_OPTIONS = {
    "wcxb-select": "--qualified-test",
    "wcxb-latency": "--candidate",
    "wcxb-score": "--evidence-root",
    "wcxb-visual": "--browser-binary",
    "wcxb-publish": "--package-only",
    "webmainbench-select": "--seed",
    "webmainbench-run": "--case-ids-file",
    "webmainbench-score": "--scorer-repo",
    "webmainbench-review": "--single-shard",
    "webmainbench-assemble": "--review",
    "webmainbench-check": "--profile",
    "webmainbench-audit": "--source-dataset",
    "webmainbench-case-correction": "--correction",
    "webmainbench-case-rescore": "--corrected-dataset",
    "webmainbench-scoring-correction": "--manifest",
    "webmainbench-teds-correction": "--prior-corrected",
    "webmainbench-teds-review": "--corrected",
    "webmainbench-latency-correction": "--cohort",
    "webmainbench-latency-tail": "--older-version",
    "webmainbench-content-review": "--work",
    "webmainbench-full-run": "--binary-source",
    "webmainbench-full-score": "--cohort",
    "webmainbench-full-assemble": "--variants",
    "webmainbench-full-check": "--authority-root",
}


def run_module(*arguments, cwd):
    path = os.pathsep.join(filter(None, [str(ROOT / "src"), os.environ.get("PYTHONPATH")]))
    return subprocess.run([sys.executable, "-m", "harness.web_fetch", *arguments], cwd=cwd,
                          env={**os.environ, "PYTHONPATH": path}, text=True, capture_output=True)


def test_every_owner_command_is_covered_and_listed_by_top_level_help(tmp_path):
    assert set(OWNER_OPTIONS) == set(OWNER_COMMANDS)
    result = run_module("--help", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    listed = " ".join(result.stdout.split())
    for name, (_, _, summary) in OWNER_COMMANDS.items():
        assert name in listed and " ".join(summary.split()) in listed
    for name in ("run", "report", "rescore", "validate", "accounts"):
        assert name in listed


@pytest.mark.parametrize("command", OWNER_OPTIONS)
def test_command_help_comes_from_its_owner_under_the_public_name(command, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["caller"])
    with pytest.raises(SystemExit) as stopped:
        main([command, "--help"])
    assert stopped.value.code == 0
    printed = capsys.readouterr().out
    assert " ".join(printed.split()).startswith(f"usage: {PROG} {command} ")
    assert OWNER_OPTIONS[command] in printed
    assert sys.argv == ["caller"]
    assert list(tmp_path.iterdir()) == []


def test_owner_rejects_an_unknown_option_the_router_passed_through(tmp_path):
    result = run_module("webmainbench-full-check", str(tmp_path), "--not-an-option", cwd=tmp_path)
    assert result.returncode == 2
    assert "--not-an-option" in result.stderr


def test_router_leaves_direct_commands_strict(tmp_path):
    result = run_module("validate", "--not-an-option", cwd=tmp_path)
    assert result.returncode == 2
    assert "--not-an-option" in result.stderr


def test_unknown_command_fails_without_running_an_owner(tmp_path):
    result = run_module("webmainbench-unknown", cwd=tmp_path)
    assert result.returncode == 2
    assert "invalid choice" in result.stderr


def test_nested_certify_stage_reaches_the_wcxb_certifier(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as stopped:
        main(["wcxb-latency", "certify", "--help"])
    assert stopped.value.code == 0
    assert "--sample-manifest" in capsys.readouterr().out


def test_sample_selection_forwards_arguments_and_keeps_the_committed_default(monkeypatch, capsys):
    calls = []

    def build_manifest(*arguments):
        calls.append(arguments)
        return {"cases": [{}, {}]}

    monkeypatch.setattr("harness.web_fetch.public_benchmark_sample.build_manifest", build_manifest)
    main(["webmainbench-select", "--source", "parent.jsonl", "--size", "2", "--seed", "fixed"])
    default = repository_root() / "evaluation/public-webmainbench-200.json"
    assert calls == [(Path("parent.jsonl"), default, 2, "fixed")]
    assert f"{default}: 2 cases" in capsys.readouterr().out


def test_latency_guard_sees_the_runner_through_each_current_entry(monkeypatch):
    listing = "\n".join([
        "101 /venv/bin/python -m harness.web_fetch webmainbench-run --provider chromium",
        "102 /venv/bin/python /venv/bin/browser-harness-eval webmainbench-run --provider chromium",
        "103 /venv/bin/python -m harness.web_fetch.webmainbench_runner --provider chromium",
        "104 /venv/bin/python -m harness.web_fetch webmainbench-score --evidence shard",
        "105 zsh -c python -m harness.web_fetch webmainbench-run --provider chromium",
    ])
    monkeypatch.setattr(latency.subprocess, "run",
                        lambda *args, **kwargs: SimpleNamespace(stdout=listing))
    assert latency.competing_benchmark_pids() == [101, 102, 103]


def test_template_registry_names_the_public_package_command():
    template = resolve_template("webfetch")
    assert template["wcxb_package"].startswith(
        "uv run --frozen python -m harness.web_fetch wcxb-publish --package-only ")


@pytest.mark.parametrize(("command", "extra"), [
    ("wcxb-visual", "headless-eval"),
    ("webmainbench-score", "webmainbench-eval"),
    ("webmainbench-review", "webmainbench-eval"),
    ("webmainbench-scoring-correction", "webmainbench-eval"),
    ("webmainbench-case-rescore", "webmainbench-eval"),
    ("webmainbench-teds-correction", "webmainbench-eval"),
    ("webmainbench-full-run", "headless-eval"),
    ("webmainbench-full-run", "webmainbench-eval"),
    ("webmainbench-full-score", "webmainbench-eval"),
])
def test_documented_uv_commands_name_the_extra_their_stage_imports(command, extra):
    root = repository_root()
    invocation = re.compile(
        r"(?:\buv|\$\(UV\)) run\b(?:(?!\brun\b)[^`\n]|\\\n)*?-m harness\.web_fetch " + re.escape(command) + r"(?![\w-])")
    found = [match.group(0) for path in DOCUMENTED_COMMAND_FILES
             for match in invocation.finditer((root / path).read_text(encoding="utf-8"))]
    assert found, f"no uv invocation of {command} is documented"
    assert all(f"--extra {extra} " in text for text in found), found
