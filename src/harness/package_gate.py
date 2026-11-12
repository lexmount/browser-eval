"""Verify the source archive and wheel from a clean installation outside the checkout."""
import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

# One smoke profile per optional install that CI protects, after the base wheel.
OPTIONAL_PROFILES = (("webmainbench-eval", "--webmainbench"), ("oss-eval", "--oss"),
                     ("reference-audit", "--reference-audit"))
# A parent `uv run` must not leak its environment or checkout into a clean install.
_PARENT_ENVIRONMENT = ("VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH")


def _only_file(directory: Path, pattern: str) -> Path:
    matches = sorted(directory.glob(pattern))
    if len(matches) != 1 or not matches[0].is_file():
        raise ValueError(f"Expected one {pattern} build output, found {len(matches)}")
    return matches[0]


def check_base_cli(root: Path, run: Callable = subprocess.run) -> None:
    """Exercise the fixture CLI and the explicit report language in the project environment."""
    python = (sys.executable, "-m", "harness.web_fetch")
    run([*python, "validate", "--providers", "fixture"], check=True, cwd=root)
    with tempfile.TemporaryDirectory() as work:
        output = Path(work) / "run"
        run([*python, "run", "--providers", "fixture", "--delay", "0", "--output", str(output)],
            check=True, cwd=root, env={**os.environ, "WEBSTONE_REPORT_LANGUAGE": "zh"})
        report = output / "report"
        contract = json.loads((report / "report-languages.json").read_text(encoding="utf-8"))
        if contract["primary_language"] != "zh" or contract["languages"] != ["zh"]:
            raise ValueError("Fixture report ignored the explicit language")
        if not (report / "en/index.html").is_file():
            raise ValueError("Fixture report lacks the English not-generated notice")


def check_distribution(root: Path, *, uv: str = "uv", run: Callable = subprocess.run) -> None:
    """Install the sdist, then the wheel and each optional profile, outside the checkout."""
    root = root.resolve()
    tool = shlex.split(uv)
    python = (root / ".python-version").read_text(encoding="utf-8").strip()
    env = {key: value for key, value in os.environ.items() if key not in _PARENT_ENVIRONMENT}
    smoke = [str(root / "tests/package_smoke.py"), "--source-root", str(root),
             "--node-html-markdown-path", str(root / "node_modules/node-html-markdown")]
    with tempfile.TemporaryDirectory() as build, tempfile.TemporaryDirectory() as work:
        build_dir, work_dir = Path(build), Path(work)

        def call(*command: str, cwd: Path = work_dir) -> None:
            run(list(command), check=True, cwd=cwd, env=env)

        call(*tool, "build", "--out-dir", str(build_dir), cwd=root)
        wheel, sdist = _only_file(build_dir, "*.whl"), _only_file(build_dir, "*.tar.gz")
        venv = work_dir / "pip-venv"
        call(*tool, "venv", "--seed", "--python", python, str(venv))
        pip_python = str(venv / "bin/python")
        call(pip_python, "-m", "pip", "install", str(sdist))
        call(pip_python, "-m", "pip", "check")
        call(str(venv / "bin/webstone"), "validate")
        call(pip_python, "-m", "pip", "install", "--force-reinstall", "--no-deps", str(wheel))
        call(pip_python, "-m", "pip", "check")
        call(pip_python, *smoke)
        for extra, flag in OPTIONAL_PROFILES:
            call(*tool, "run", "--no-project", "--isolated", "--python", python,
                 "--with", f"{wheel}[{extra}]", "python", *smoke, flag)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--uv", default="uv", help="uv command, as the Makefile's UV variable")
    args = parser.parse_args(argv)
    check_base_cli(args.root)
    check_distribution(args.root, uv=args.uv)
