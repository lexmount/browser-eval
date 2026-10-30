"""Select immutable Hugging Face editions without rewriting historical evidence."""
from __future__ import annotations
from harness.distribution import repository_root, source_path

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from harness.benchmarks.data import canonical_repository, fetch_archive, read_hf_file, unpack
from harness.benchmarks.publication import CARDS, DatasetRelease


def validate_binding(root: Path, name: str = "lexbench-webfetch") -> str:
    track = name.removeprefix("lexbench-")
    lock = json.loads(source_path("harness/benchmarks/data-lock.json", root=root).read_bytes())
    selected = lock["datasets"][track]
    path = root / "evaluation" / (name + ".json")
    imported = json.loads(path.read_bytes()) if path.exists() else None
    return validate_selected_binding(selected, imported)


def validate_selected_binding(selected: dict, imported: dict | None = None) -> str:
    revision = selected["revision"]
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Dataset must select a full Hugging Face commit")
    if imported is not None:
        if (imported["revision"] != revision
                or not imported["repository"].startswith("https://huggingface.co/datasets/")
                or canonical_repository(imported["repository"].removeprefix("https://huggingface.co/datasets/"))
                != canonical_repository(selected["repository"])):
            raise ValueError("Dataset lock and import binding disagree; run make pin-dataset")
        for relative, expected in imported.get("definition_files", {}).items():
            if selected["files"]["definitions/" + relative]["sha256"] != expected:
                raise ValueError("Dataset definition binding disagrees with its locked snapshot")
    return revision


def pin(root: Path, track: str, revision: str) -> list[str]:
    return pin_many(root, [(track, revision)])[track]


def pin_many(root: Path, selections: list[tuple[str, str]]) -> dict[str, list[str]]:
    """Validate coordinated editions together and publish one existing binding transaction."""
    if not selections or len({track for track, _ in selections}) != len(selections):
        raise ValueError("Select each benchmark track exactly once")
    if any(not re.fullmatch(r"[0-9a-f]{40}", revision) for _, revision in selections):
        raise ValueError("REV must be a full lowercase Hugging Face commit")
    lock_path = source_path("harness/benchmarks/data-lock.json", root=root)
    lock = json.loads(lock_path.read_bytes())
    candidate = dict(lock["datasets"])
    updates, changes, releases = {}, {}, []
    for track, revision in selections:
        selected, imported, differences, release = _prepare_pin(root, track, revision, candidate.get(track))
        candidate[track] = selected
        updates.update(imported)
        changes[track] = differences
        releases.append(release)
    validate_selections(candidate)
    for release in releases:
        list(release.records(selections=candidate))
    lock["datasets"] = candidate
    updates[lock_path] = lock
    publish_bindings(updates)
    return changes


def _prepare_pin(root: Path, track: str, revision: str, before: dict | None) -> tuple:
    if before is None:
        registered = json.loads((CARDS / "metadata.json").read_bytes())
        if track not in registered:
            raise ValueError("Unknown benchmark track: " + track)
        before = {"repository": registered[track]["repo_id"]}
    raw_manifest = read_hf_file(before["repository"], revision, "manifest.json", 2_000_000)
    manifest = json.loads(raw_manifest)
    if manifest.get("schema") != "browser-eval-dataset-release/1" or manifest.get("track") != track:
        raise ValueError("Dataset release identity mismatch")
    archive = manifest.get("archive")
    if archive is None:
        known = next((item for item in [before, *before.get("history", [])]
                      if item.get("revision") == revision), None)
        if known is None:
            raise ValueError("Historical release has no known immutable archive binding")
        archive = known["archive"]
    raw = fetch_archive(before["repository"], revision, archive)
    selected = {
        "repository": before["repository"], "revision": revision,
        "source_revision": manifest["source_revision"], "archive": archive,
        "datasets": manifest["datasets"],
        "files": {name: item for name, item in manifest["files"].items() if not name.startswith("data/")},
    }
    for option in ("dataset_sources", "default_dataset", "scorer"):
        if option in before:
            selected[option] = copy.deepcopy(before[option])
    previous = ([{k: v for k, v in before.items() if k != "history"}]
                if "revision" in before else []) + before.get("history", [])
    history = {item["revision"]: item for item in previous if item["revision"] != revision}
    selected["history"] = list(history.values())
    authorities = copy.deepcopy(before.get("source_editions", {}))
    for item in reversed(previous):
        if item.get("source_revision"):
            authorities.setdefault(item["source_revision"], item["revision"])
    authorities.setdefault(selected["source_revision"], revision)
    selected["source_editions"] = authorities
    import_path = root / "evaluation" / ("lexbench-" + track + ".json")
    updates = {}
    with tempfile.TemporaryDirectory(prefix="browser-eval-dataset-pin-") as directory:
        unpack(raw, Path(directory), selected)
        release = DatasetRelease(track, selected["source_revision"],
                                 {name: (Path(directory) / name).read_bytes() for name in selected["files"]})
        if release.datasets() != selected["datasets"]:
            raise ValueError("Release manifest dataset bindings differ from its registry")
        if import_path.exists():
            imported = refresh_binding(json.loads(import_path.read_bytes()), revision, Path(directory) / "definitions")
            imported["kind"] = "huggingface_benchmark_binding"
            imported["repository"] = "https://huggingface.co/datasets/" + selected["repository"]
            imported["source_revision"] = selected["source_revision"]
            updates[import_path] = imported
    changes = binding_changes(before, selected)
    validate_selected_binding(selected, updates.get(import_path))
    return selected, updates, changes, release


def publish_bindings(updates: dict[Path, dict]) -> None:
    """Stage replacements and restore original bindings on publication failure."""
    encoded = {path: (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
               for path, value in updates.items()}
    staged, published, restored = [], [], set()
    completed = False
    try:
        for path, raw in encoded.items():
            directory = Path(tempfile.mkdtemp(prefix=".dataset-pin-", dir=path.parent))
            candidate, backup = directory / "next", directory / "original"
            staged.append((path, candidate, backup, directory))
            shutil.copy2(path, backup)
            candidate.write_bytes(raw)
            candidate.chmod(path.stat().st_mode & 0o777)
        try:
            for path, candidate, backup, _ in staged:
                # Replacement may take effect before an interruption is reported.
                published.append((path, backup))
                os.replace(candidate, path)
            completed = True
        except BaseException:
            failures = []
            for path, backup in reversed(published):
                try:
                    os.replace(backup, path)
                    restored.add(path)
                except OSError as error:
                    failures.append((backup, error))
            if failures:
                retained = '; '.join(f'original retained at {backup}' for backup, _ in failures)
                raise OSError('Binding rollback failed; ' + retained) from failures[0][1]
            raise
    finally:
        for path, _, _, directory in staged:
            if completed or path in restored or not any(item[0] == path for item in published):
                shutil.rmtree(directory)


def validate_selections(selections: dict) -> None:
    """Check the composed inputs consumers will load before changing bindings."""
    for track, selected in selections.items():
        datasets = selected.get("datasets", {})
        default = selected.get("default_dataset")
        if default is not None and default not in datasets:
            raise ValueError(f"Default dataset is absent from {track}: {default}")
        for identity, source in selected.get("dataset_sources", {}).items():
            consumer = datasets.get(identity)
            provider = selections.get(source, {})
            supplied = provider.get("datasets", {}).get(identity)
            if consumer is None or supplied is None or consumer != supplied:
                raise ValueError(f"Incompatible dataset source: {track}/{identity} -> {source}")
            file = provider.get("files", {}).get("definitions/" + supplied["path"])
            if file is None or file["sha256"] != consumer["sha256"]:
                raise ValueError(f"Dataset source file binding mismatch: {source}/{identity}")


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
            if len(old) != len(new):
                changes.append(f"{path}: list length {len(old)} -> {len(new)}")
            else:
                for index, (left, right) in enumerate(zip(old, new, strict=True)):
                    visit(left, right, path + "/" + str(index))
        elif old != new:
            changes.append(f"{path}: {old} -> {new}")
    visit(before, after, "binding")
    return changes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--track')
    parser.add_argument('--revision')
    parser.add_argument('--select', action='append', metavar='TRACK=COMMIT',
                        help='Repeat to select mutually dependent editions in one transaction')
    parser.add_argument('--root', type=Path, default=repository_root())
    args = parser.parse_args()
    if args.select:
        if args.track is not None or args.revision is not None:
            parser.error('--select cannot be combined with --track/--revision')
        selections = []
        for value in args.select:
            track, separator, revision = value.partition('=')
            if not separator or not track or not revision:
                parser.error('--select requires TRACK=COMMIT')
            selections.append((track, revision))
        changes = pin_many(args.root, selections)
        for track, revision in selections:
            print(f'Pinned {track} to {revision}; {len(changes[track])} binding changes')
    else:
        if args.track is None or args.revision is None:
            parser.error('--track and --revision are required without --select')
        changes = pin(args.root, args.track, args.revision)
        print(f'Pinned {args.track} to {args.revision}; {len(changes)} binding changes')


if __name__ == '__main__':
    main()
