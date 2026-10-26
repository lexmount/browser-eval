"""Maintain the current evaluator pin without rewriting historical evidence."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

NAME = "lexbench-webfetch"
HINT = "Run make bump-lexbench REV=<merged-commit>."
ROOT = Path(__file__).resolve().parents[1]


def _subdirectory(value: str) -> str:
    if value and (not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", value)
                  or any(part in {'.', '..'} for part in value.split('/'))):
        raise ValueError("LexBench subdirectory must stay within the repository")
    return value


def project_pin(project: dict, name: str = NAME) -> tuple[str, str, str]:
    matches = [item for item in project["project"]["dependencies"]
               if re.match(rf"^{re.escape(name)}\s*@", item)]
    if len(matches) != 1:
        raise ValueError("Expected one direct LexBench Git dependency. " + HINT)
    match = re.fullmatch(
        rf"{re.escape(name)}\s*@\s*git\+([^#]+)@([0-9a-f]{{40}})(?:#(.*))?", matches[0])
    if not match:
        raise ValueError("LexBench must use a full immutable Git commit. " + HINT)
    fragment = parse_qs(match.group(3) or '', keep_blank_values=True)
    if fragment and (set(fragment) != {'subdirectory'} or len(fragment['subdirectory']) != 1):
        raise ValueError("Invalid LexBench subdirectory binding")
    directory = fragment.get('subdirectory', [''])[0]
    return match.group(1), match.group(2), _subdirectory(directory)


def validate_binding(root: Path, name: str = NAME) -> str:
    repository, revision, directory = project_pin(
        tomllib.loads((root / "pyproject.toml").read_text()), name)
    lock = tomllib.loads((root / "uv.lock").read_text())
    packages = [item for item in lock["package"] if item["name"] == name]
    binding = json.loads((root / "evaluation" / (name + '.json')).read_text())
    if len(packages) != 1:
        raise ValueError("Expected one locked LexBench package. " + HINT)
    source = urlsplit(packages[0].get("source", {}).get("git", ""))
    locked_repository = source._replace(query="", fragment="").geturl()
    if (locked_repository != repository or source.fragment != revision
            or parse_qs(source.query).get("rev") != [revision]
            or parse_qs(source.query).get("subdirectory", ['']) != [directory]
            or binding.get("subdirectory", '') != directory
            or binding["revision"] != revision
            or binding["repository"].removesuffix(".git") != repository.removesuffix(".git")):
        raise ValueError("LexBench project, lock and definition binding disagree. " + HINT)
    return revision


def refresh_binding(binding: dict, revision: str, definitions: Path) -> dict:
    updated = copy.deepcopy(binding)
    updated["revision"] = revision

    def read(relative: str) -> bytes:
        path = definitions / relative
        if not path.resolve().is_relative_to(definitions.resolve()) or path.is_symlink():
            raise ValueError("Definition path escapes installed package: " + relative)
        return path.read_bytes()

    for relative in updated.get("definition_files", {}):
        updated["definition_files"][relative] = hashlib.sha256(read(relative)).hexdigest()
    if 'datasets' in updated:
        updated['datasets'] = json.loads(read('registry.json'))['datasets']
        for identity, dataset in updated['datasets'].items():
            raw = read(dataset['path'])
            if hashlib.sha256(raw).hexdigest() != dataset['sha256'] or len(json.loads(raw)['cases']) != dataset['count']:
                raise ValueError('Benchmark registry does not bind its dataset: ' + identity)
        return updated
    for suite in updated["suites"]:
        directory = suite["historical_directory"]
        for name in suite["files"]:
            suite["files"][name] = hashlib.sha256(read(directory + "/" + name)).hexdigest()
        tasks = json.loads(read(directory + "/tasks.json"))
        suite["dataset_id"] = tasks["id"]
        suite["case_count"] = len(tasks["cases"])
    return updated


def binding_changes(before: dict, after: dict) -> list[str]:
    changes = []
    def visit(old, new, path):
        if isinstance(old, dict) and isinstance(new, dict):
            for key in sorted(old.keys() | new.keys()):
                visit(old.get(key), new.get(key), path + "/" + key)
        elif isinstance(old, list) and isinstance(new, list):
            for index, (left, right) in enumerate(zip(old, new, strict=True)):
                visit(left, right, path + "/" + str(index))
        elif old != new:
            changes.append(f"{path}: {old} -> {new}")
    visit(before, after, "binding")
    return changes


def run(command: list[str], cwd: Path) -> str:
    return subprocess.run(command, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE).stdout.strip()


def require_merged_revision(repository: str, revision: str, directory: Path) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("REV must be a full lowercase Git commit hash")
    run(["git", "init", "--quiet", str(directory)], directory.parent)
    run(["git", "fetch", "--quiet", repository, "refs/heads/main"], directory)
    if subprocess.run(["git", "merge-base", "--is-ancestor", revision, "FETCH_HEAD"],
                      cwd=directory, capture_output=True).returncode != 0:
        raise ValueError("Requested LexBench revision is not in upstream main history")


def bump(root: Path, revision: str, *, uv: str = "uv", benchmark: str = NAME,
         repository: str | None = None, subdirectory: str | None = None) -> list[str]:
    project_path = root / "pyproject.toml"
    text = project_path.read_text()
    old_repository, old_revision, old_directory = project_pin(tomllib.loads(text), benchmark)
    repository = repository if repository is not None else old_repository
    directory = _subdirectory(subdirectory if subdirectory is not None else old_directory)
    relative_binding = 'evaluation/' + benchmark + '.json'
    binding_path = root / relative_binding
    before = json.loads(binding_path.read_text())
    with tempfile.TemporaryDirectory(prefix="browser-eval-dependency-") as temporary:
        staging = Path(temporary)
        require_merged_revision(repository, revision, staging / "upstream")
        project = staging / "project"
        project.mkdir()
        (project / "evaluation").mkdir()
        for name in ("uv.lock", ".python-version", "README.md", "LICENSE"):
            shutil.copyfile(root / name, project / name)
        old_url = f"git+{old_repository}@{old_revision}"
        if old_directory:
            old_url += '#subdirectory=' + old_directory
        if text.count(old_url) != 1:
            raise ValueError("Ambiguous dependency declaration")
        new_url = f"git+{repository}@{revision}"
        if directory:
            new_url += '#subdirectory=' + directory
        (project / "pyproject.toml").write_text(text.replace(old_url, new_url))
        run([uv, "lock", "--project", str(project)], project)
        run([uv, "sync", "--frozen", "--no-install-project", "--project", str(project)], project)
        definitions = Path(run([
            uv, "run", "--frozen", "--no-sync", "python", "-c",
            f"from {benchmark.replace('-', '_')}.inputs import definition_path; print(definition_path({'registry.json' if benchmark == 'lexbench-websearch' else 'evaluation'!r}).parent)",
        ], project))
        after = refresh_binding(before, revision, definitions)
        after['repository'] = repository.removesuffix('.git')
        if directory:
            after['subdirectory'] = directory
        else:
            after.pop('subdirectory', None)
        (project / relative_binding).write_text(
            json.dumps(after, ensure_ascii=False, indent=2) + "\n")
        validate_binding(project, benchmark)
        # Network, installation and package validation all finish before changing the checkout.
        destinations = ("pyproject.toml", "uv.lock", relative_binding)
        originals = {name: (root / name).read_bytes() for name in destinations}
        try:
            for name in destinations:
                (root / name).write_bytes((project / name).read_bytes())
        except OSError:
            for name, raw in originals.items():
                (root / name).write_bytes(raw)
            raise
    return binding_changes(before, after)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--uv", default="uv")
    parser.add_argument('--benchmark', choices=['lexbench-webfetch', 'lexbench-websearch'], default=NAME)
    parser.add_argument('--repository', help='Git repository for a source migration')
    parser.add_argument('--subdirectory', help='Package directory within that repository')
    args = parser.parse_args(argv)
    for change in bump(ROOT, args.revision, uv=args.uv, benchmark=args.benchmark,
                       repository=args.repository, subdirectory=args.subdirectory):
        print(change)
    print("Verified current dependency binding. Historical evidence was not changed.")
