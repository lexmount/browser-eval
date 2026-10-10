from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from collections.abc import Iterable
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
    config: dict[str, Any], resolve_token, *, required_fields: Iterable[str] = (),
    required_paths: Iterable[str] = (), identical_files: Iterable[tuple[str, str]] = (),
) -> tuple[list[str], dict[str, Any] | None]:
    """Validate and fingerprint a local implementation without publishing local paths."""
    missing: list[str] = []
    for field in required_fields:
        if not isinstance(config.get(field), str) or not config[field].strip():
            missing.append(field)
    declared_paths = config.get('required_paths', [])
    if not isinstance(declared_paths, list) or any(
        not isinstance(path, str) or not path.strip() for path in declared_paths
    ):
        missing.append('required_paths must be a list of nonempty paths')
        declared_paths = []
    if not set(required_paths).issubset(declared_paths):
        missing.append('required runtime paths')
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
    for raw in declared_paths:
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

    for source, deployed in identical_files:
        if not file_hashes.get(source) or file_hashes.get(source) != file_hashes.get(deployed):
            missing.append(f'runtime files differ: {source}, {deployed}')

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

    package_manager: dict[str, str] | None = None
    expected_package_manager = str(
        config.get("expected_package_manager_version", "")
    ).strip()
    raw_package_manager = str(config.get("package_manager_command", "")).strip()
    if expected_package_manager or raw_package_manager:
        manager = _command_executable(
            [raw_package_manager] if raw_package_manager else [], repo, resolve_token
        )
        if not expected_package_manager:
            missing.append("expected_package_manager_version")
        if manager is None:
            missing.append("package manager executable")
        else:
            try:
                actual_package_manager = subprocess.check_output(
                    [str(manager), "--version"], text=True, stderr=subprocess.DEVNULL,
                    timeout=10,
                    env={**os.environ, **({"PATH": str(executable.parent) + os.pathsep
                                           + os.environ.get("PATH", "")}
                                          if executable is not None else {})},
                ).strip()
            except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
                missing.append(f"package manager {expected_package_manager}")
            else:
                if actual_package_manager != expected_package_manager:
                    missing.append(f"package manager {expected_package_manager}")
                package_manager = {
                    "name": str(config.get("package_manager_name", "pnpm")),
                    "version": actual_package_manager,
                    "executable_sha256": _sha256(manager),
                }

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
    if package_manager is not None:
        identity["package_manager"] = package_manager
    if python is not None:
        identity["python"] = python
    return missing, None if missing else identity


def version_number(value):
    match = re.search(r"\d+(?:\.\d+)+", value or "")
    return match.group(0) if match else value


def require_expected_provider_versions(identities, configs):
    """Fail before measurement if a versioned provider slot is mislabeled."""
    for name, identity in identities.items():
        expected = configs[name].get("expected_version")
        if expected is None:
            continue
        if not isinstance(expected, str) or not re.fullmatch(r"\d+(?:\.\d+)+", expected):
            raise ValueError(name + ": expected_version must be a numeric version")
        reported = version_number(identity.get("reported_version"))
        if reported != expected:
            raise ValueError(
                f"{name}: expected version {expected}, reported {reported or 'unavailable'}"
            )
