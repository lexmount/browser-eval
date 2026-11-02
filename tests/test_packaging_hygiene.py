"""Public packaging, Makefile and platform capability boundaries."""
import json
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml

from harness import distribution, package_gate, platform_capabilities
from tests.package_smoke import verify_package_tree

ROOT = Path(__file__).resolve().parents[1]
MAKEFILE = (ROOT / "Makefile").read_text()


def make(*arguments, cwd=ROOT):
    executable = shutil.which("make")
    if executable is None:
        pytest.skip("Make is not installed")
    return subprocess.run([executable, "--no-print-directory", "-f", str(ROOT / "Makefile"), *arguments],
                          cwd=cwd, capture_output=True, text=True)


def bundled():
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["browser-eval"]["bundled-data"]


def test_bundled_data_exists_and_reaches_the_source_archive():
    manifest = {line.split()[1] for line in (ROOT / "MANIFEST.in").read_text().splitlines()
                if line.startswith("include ")}
    # setuptools always archives pyproject.toml.
    assert manifest | {"pyproject.toml"} == set(bundled())
    assert all((ROOT / relative).is_file() for relative in bundled())
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert "data-files" not in project["tool"]["setuptools"]


def test_checkout_reads_bundled_data_from_its_repository_path():
    for relative in bundled():
        assert distribution.bundled_data_path(relative) == ROOT / relative


@pytest.mark.parametrize("relative", ["/etc/hosts", "../secret.json", ""])
def test_bundled_data_rejects_paths_outside_the_declared_tree(relative):
    with pytest.raises(ValueError):
        distribution.bundled_data_path(relative)


def installed_tree(tmp_path, monkeypatch):
    """Point the reader at a synthetic installed package outside any checkout."""
    package = tmp_path / "site-packages/harness"
    data = package / "distribution_data/configs"
    data.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    monkeypatch.setattr(distribution, "repository_root", lambda: tmp_path / "site-packages")
    monkeypatch.setattr(distribution.importlib.resources, "files", lambda name: package)
    return data


def test_installed_reader_uses_package_data_and_rejects_missing_or_linked(tmp_path, monkeypatch):
    data = installed_tree(tmp_path, monkeypatch)
    (data / "real.json").write_text("{}")
    assert distribution.bundled_data_path("configs/real.json") == data / "real.json"
    with pytest.raises(FileNotFoundError):
        distribution.bundled_data_path("configs/absent.json")
    (data / "linked.json").symlink_to(data / "real.json")
    with pytest.raises(ValueError, match="symlinked"):
        distribution.bundled_data_path("configs/linked.json")


@pytest.mark.parametrize("damage", ["none", "missing", "changed", "extra"])
def test_package_tree_requires_exact_bundled_data(tmp_path, damage):
    source, installed = tmp_path / "source", tmp_path / "installed"
    (source / "src/harness").mkdir(parents=True)
    (source / "src/harness/__init__.py").write_text("")
    (source / "uv.lock").write_text("lock")
    (source / "pyproject.toml").write_text(
        '[tool.setuptools.package-dir]\n"" = "src"\n'
        '[tool.browser-eval]\nbundled-data = ["uv.lock"]\n')
    (installed / "distribution_data").mkdir(parents=True)
    (installed / "__init__.py").write_text("")
    copy = installed / "distribution_data/uv.lock"
    copy.write_text("lock")
    if damage == "missing":
        copy.unlink()
    elif damage == "changed":
        copy.write_text("other")
    elif damage == "extra":
        (installed / "distribution_data/private.json").write_text("{}")
    if damage == "none":
        verify_package_tree(source, installed)
    else:
        with pytest.raises(ValueError):
            verify_package_tree(source, installed)


def fake_build(outputs):
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        if command[1] == "build":
            directory = Path(command[command.index("--out-dir") + 1])
            for name in outputs:
                (directory / name).write_text("")
        return subprocess.CompletedProcess(command, 0)

    return commands, run


def test_distribution_gate_installs_every_profile_with_the_pinned_python(monkeypatch):
    monkeypatch.setenv("VIRTUAL_ENV", "/parent")
    monkeypatch.setenv("PYTHONPATH", "/checkout")
    environments = []
    commands, recording = fake_build(["one.whl", "one.tar.gz"])

    def run(command, **kwargs):
        environments.append(kwargs["env"])
        return recording(command, **kwargs)

    package_gate.check_distribution(ROOT, uv="uv", run=run)
    version = (ROOT / ".python-version").read_text().strip()
    assert commands[0][:2] == ["uv", "build"]
    assert commands[1][:4] == ["uv", "venv", "--seed", "--python"] and commands[1][4] == version
    extras = {extra for extra, _ in package_gate.OPTIONAL_PROFILES}
    assert {re.search(r"\[(.+)\]$", part)[1] for command in commands for part in command
            if part.endswith("]")} == extras
    for command in commands[-len(extras):]:
        assert command[command.index("--python") + 1] == version
    assert not any("VIRTUAL_ENV" in env or "PYTHONPATH" in env for env in environments)


@pytest.mark.parametrize("outputs", [["one.tar.gz"], ["one.whl"], ["a.whl", "b.whl", "a.tar.gz"]])
def test_distribution_gate_rejects_incomplete_or_ambiguous_builds(outputs):
    _, run = fake_build(outputs)
    with pytest.raises(ValueError, match="build output"):
        package_gate.check_distribution(ROOT, run=run)


def test_distribution_gate_stops_at_the_first_failed_install():
    commands, recording = fake_build(["one.whl", "one.tar.gz"])

    def run(command, **kwargs):
        recording(command, **kwargs)
        if command[1:3] == ["-m", "pip"]:
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0)

    with pytest.raises(subprocess.CalledProcessError):
        package_gate.check_distribution(ROOT, run=run)
    assert commands[-1][1:4] == ["-m", "pip", "install"]


@pytest.mark.parametrize("contract,notice,accepted", [
    ({"primary_language": "zh", "languages": ["zh"]}, True, True),
    ({"primary_language": "en", "languages": ["en", "zh"]}, True, False),
    ({"primary_language": "zh", "languages": ["zh"]}, False, False),
])
def test_base_cli_check_requires_the_explicit_language_contract(contract, notice, accepted):
    def run(command, **kwargs):
        if "run" in command:
            assert kwargs["env"]["BROWSER_EVAL_REPORT_LANGUAGE"] == "zh"
            report = Path(command[command.index("--output") + 1]) / "report"
            (report / "en").mkdir(parents=True)
            (report / "report-languages.json").write_text(json.dumps(contract))
            if notice:
                (report / "en/index.html").write_text("")
        return subprocess.CompletedProcess(command, 0)

    if accepted:
        package_gate.check_base_cli(ROOT, run=run)
    else:
        with pytest.raises(ValueError):
            package_gate.check_base_cli(ROOT, run=run)


def test_makefile_declares_every_target_phony_exactly_once():
    assert MAKEFILE.count(".PHONY") == 1
    block = re.search(r"^\.PHONY:((?:.*\\\n)*.*)$", MAKEFILE, re.M)[1]
    declared = block.replace("\\\n", " ").split()
    targets = re.findall(r"^([a-z][a-z0-9-]*):(?!=)", MAKEFILE, re.M)
    assert sorted(declared) == sorted(targets) and len(targets) == len(set(targets))


def test_help_documents_every_target_and_is_the_default():
    result = make()
    assert result.returncode == 0, result.stderr
    listed = {line.split()[0] for line in result.stdout.splitlines()}
    assert listed == set(re.findall(r"^([a-z][a-z0-9-]*):(?!=)", MAKEFILE, re.M))


def test_make_ci_runs_each_ci_job():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    ci = make("-n", "ci", "PYTEST_ARGS=-q").stdout
    for job in ("checks", "package"):
        for step in workflow["jobs"][job]["steps"]:
            for target in re.findall(r"^make ([a-z-]+)", step.get("run", ""), re.M):
                if target not in {"docs-check", "setup", "setup-test"}:
                    assert f"{target}:" in MAKEFILE and target in re.search(
                        r"^ci: (.*?) ##", MAKEFILE, re.M)[1]
    browser = workflow["jobs"]["browser"]["steps"][-1]["run"]
    for path in re.search(r"pytest (.*?) -q", browser)[1].split():
        assert path in ci
    assert "scripts/check_package.py" in ci and "shellcheck" in ci


@pytest.mark.parametrize("target", ["report", "report-check", "wcxb-release-check", "trajectory-replay-publish",
                                    "public-webarena-report", "webmainbench-assemble",
                                    "webfetch-diagnostic-report"])
def test_required_profile_is_rejected_before_any_tool_runs(target, tmp_path):
    (tmp_path / "bin").mkdir()
    fake = tmp_path / "bin/uv"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o700)
    result = make(target, f"UV={fake}", "EVIDENCE=/synthetic", "OUTPUT=/synthetic", "RUN_ROOT=/synthetic",
                  "SOURCE=/synthetic", "REVIEW=/synthetic", cwd=ROOT)
    assert result.returncode == 2 and "PROFILE=/absolute/path/to/profile.json is required" in result.stderr


def test_format_requires_explicit_files():
    result = make("format")
    assert result.returncode == 2 and "FILES=" in result.stderr


def test_clean_keeps_reports_and_environments():
    recipe = make("-n", "clean").stdout
    assert "build dist" in recipe
    assert not re.search(r"\b(reports|node_modules|\.venv|\.env)\b", recipe)


def test_python_policy_agrees_across_project_lock_and_ci():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    pinned = (ROOT / ".python-version").read_text().strip()
    assert pinned == "3.11"
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    versions = {step["with"]["python-version"] for job in workflow["jobs"].values()
                for step in job["steps"] if "python-version" in step.get("with", {})}
    assert versions == {pinned}
    declared = project["project"]["requires-python"]
    assert declared == f">={pinned},<3.12"
    assert tomllib.loads((ROOT / "uv.lock").read_text())["requires-python"] == f"=={pinned}.*"
    for readme in ("README.md", "docs/README.zh-CN.md"):
        text = (ROOT / readme).read_text()
        assert "3.14" not in text and "Python 3.11+" not in text


@pytest.fixture
def linux(monkeypatch):
    monkeypatch.setattr(platform_capabilities.platform, "system", lambda: "Linux")


def test_macos_features_name_themselves_on_another_host(linux):
    with pytest.raises(platform_capabilities.MacOSRequiredError, match="native DNS requires macOS; this host runs Linux"):
        platform_capabilities.require_macos("native DNS")
    assert not platform_capabilities.sandbox_exec_available()
    with pytest.raises(platform_capabilities.MacOSRequiredError, match="requires macOS"):
        platform_capabilities.sandbox_exec_command("(version 1)")


def test_missing_macos_tool_and_sandbox_are_named(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_capabilities.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(platform_capabilities.shutil, "which", lambda tool: None)
    with pytest.raises(platform_capabilities.MacOSRequiredError, match="missing: top, memory_pressure"):
        platform_capabilities.require_macos("sampling", ("top", "memory_pressure"))
    monkeypatch.setattr(platform_capabilities, "SANDBOX_EXEC", tmp_path / "absent")
    assert not platform_capabilities.sandbox_exec_available()
    with pytest.raises(platform_capabilities.MacOSRequiredError, match="absent"):
        platform_capabilities.sandbox_exec_command("(version 1)")
    present = tmp_path / "present"
    present.write_text("")
    monkeypatch.setattr(platform_capabilities, "SANDBOX_EXEC", present)
    assert platform_capabilities.sandbox_exec_available()
    assert platform_capabilities.sandbox_exec_command("(version 1)") == [str(present), "-p", "(version 1)"]


def test_macos_callers_stop_before_external_tools(linux, monkeypatch, tmp_path):
    from harness.web_fetch import network_dns, webmainbench_latency_correction

    def unexpected(*args, **kwargs):
        raise AssertionError("macOS tool launched on another host")

    monkeypatch.setattr(subprocess, "run", unexpected)
    for call in (webmainbench_latency_correction.host_sample,
                 lambda: network_dns.validate_native_dns_library(tmp_path / "dns.dylib"),
                 lambda: network_dns.build_native_dns_library(tmp_path / "dns.dylib")):
        with pytest.raises(platform_capabilities.MacOSRequiredError):
            call()
