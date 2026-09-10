from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _command_executable(command: list[Any], repo: Path, resolve_token) -> Path | None:
    if not command:
        return None
    value = resolve_token(command[0])
    if os.sep in value or (os.altsep and os.altsep in value):
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = repo / path
        # Keep the invoked path intact.  Resolving a virtualenv's ``python``
        # symlink to the base interpreter changes Python's prefix discovery and
        # makes installed distributions disappear from importlib.metadata.
        path = path.absolute()
        return path if path.is_file() and os.access(path, os.X_OK) else None
    found = shutil.which(value)
    return Path(found).absolute() if found else None


def validate_runtime_identity(
    config: dict[str, Any], resolve_token
) -> tuple[list[str], dict[str, Any] | None]:
    """Validate and fingerprint a local implementation without publishing local paths."""
    missing: list[str] = []
    raw_repo = str(config.get("repo_path", "")).strip()
    if not raw_repo:
        return ["repo_path"], None
    repo = Path(raw_repo).expanduser().resolve()
    if not repo.is_dir():
        return ["repo_path"], None

    command = config.get("command")
    executable: Path | None = None
    if command is not None:
        if not isinstance(command, list) or not command:
            missing.append("command")
        else:
            executable = _command_executable(command, repo, resolve_token)
            if executable is None:
                missing.append("command executable")

    expected_revision = str(config.get("expected_repo_revision", "")).strip()
    repository: dict[str, Any] | None = None
    if expected_revision:
        try:
            head = subprocess.check_output(
                ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
            tracked_status = subprocess.check_output(
                ["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=no"],
                text=True, stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.CalledProcessError):
            missing.append("git checkout")
        else:
            if head != expected_revision:
                missing.append(f"repo revision {expected_revision}")
            if tracked_status:
                missing.append("clean tracked checkout")
            repository = {"head": head, "tracked_status": tracked_status}

    harness_root = Path(__file__).resolve().parents[3]
    file_hashes: dict[str, str] = {}
    for raw in config.get("required_paths", []):
        resolved = Path(resolve_token(raw)).expanduser().resolve()
        if not resolved.is_file():
            missing.append(f"required path {raw}")
            continue
        if resolved.is_relative_to(repo):
            public_name = "repo/" + resolved.relative_to(repo).as_posix()
        elif resolved.is_relative_to(harness_root):
            public_name = "harness/" + resolved.relative_to(harness_root).as_posix()
        else:
            missing.append(f"required path outside runtime roots {raw}")
            continue
        file_hashes[public_name] = _sha256(resolved)

    node: dict[str, str] | None = None
    expected_node = str(config.get("expected_node_version", "")).strip()
    if expected_node:
        if executable is None:
            missing.append(f"Node {expected_node}")
        else:
            try:
                actual_node = subprocess.check_output(
                    [str(executable), "--version"], text=True, stderr=subprocess.DEVNULL,
                    timeout=10,
                ).strip()
            except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
                missing.append(f"Node {expected_node}")
            else:
                if actual_node != expected_node:
                    missing.append(f"Node {expected_node}")
                node = {"version": actual_node, "executable_sha256": _sha256(executable)}

    python: dict[str, Any] | None = None
    expected_python = str(config.get("expected_python_version", "")).strip()
    expected_libraries = config.get("expected_library_versions", {})
    if expected_python or expected_libraries:
        if executable is None:
            missing.append("Python runtime")
        elif not isinstance(expected_libraries, dict):
            missing.append("expected_library_versions")
        else:
            try:
                actual_python = subprocess.check_output(
                    [str(executable), "--version"], text=True, stderr=subprocess.STDOUT,
                    timeout=10,
                ).strip()
                package_names = sorted(expected_libraries)
                package_output = subprocess.check_output(
                    [str(executable), "-c",
                     "import importlib.metadata as m,json,sys;print(json.dumps({n:m.version(n) for n in sys.argv[1:]}))",
                     *package_names], text=True, stderr=subprocess.STDOUT, timeout=20,
                )
                actual_libraries = json.loads(package_output)
            except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired,
                    ValueError, json.JSONDecodeError):
                missing.append("Python runtime identity")
            else:
                if expected_python and actual_python != expected_python:
                    missing.append(expected_python)
                if actual_libraries != expected_libraries:
                    missing.append("locked Python libraries")
                python = {"version": actual_python,
                          "executable_sha256": _sha256(executable),
                          "libraries": actual_libraries}

    identity: dict[str, Any] = {"files_sha256": dict(sorted(file_hashes.items()))}
    if repository is not None:
        identity["repository"] = repository
    if node is not None:
        identity["node"] = node
    if python is not None:
        identity["python"] = python
    return missing, None if missing else identity
