"""Select immutable Hugging Face editions without rewriting historical evidence."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import re
import tempfile
from pathlib import Path
from urllib.request import urlopen
from harness.benchmarks.data import unpack


def validate_binding(root: Path, name: str = "lexbench-webfetch") -> str:
    track = name.removeprefix("lexbench-")
    lock = json.loads((root / "harness/benchmarks/data-lock.json").read_bytes())
    selected = lock["datasets"][track]
    revision = selected["revision"]
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Dataset must select a full Hugging Face commit")
    path = root / "evaluation" / (name + ".json")
    if path.exists():
        imported = json.loads(path.read_bytes())
        if (imported["revision"] != revision
                or imported["repository"] != "https://huggingface.co/datasets/" + selected["repository"]):
            raise ValueError("Dataset lock and import binding disagree; run make pin-dataset")
        for relative, expected in imported.get("definition_files", {}).items():
            if selected["files"]["definitions/" + relative]["sha256"] != expected:
                raise ValueError("Dataset definition binding disagrees with its locked snapshot")
    return revision


def pin(root: Path, track: str, revision: str) -> list[str]:
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("REV must be a full lowercase Hugging Face commit")
    lock_path = root / "harness/benchmarks/data-lock.json"
    lock = json.loads(lock_path.read_bytes())
    before = lock["datasets"][track]
    url = f"https://huggingface.co/datasets/{before['repository']}/resolve/{revision}/"
    with urlopen(url + "manifest.json", timeout=60) as response:
        raw_manifest = response.read(2_000_001)
    if len(raw_manifest) > 2_000_000:
        raise ValueError("Dataset release manifest is too large")
    manifest = json.loads(raw_manifest)
    if manifest.get("schema") != "browser-eval-dataset-release/1" or manifest.get("track") != track:
        raise ValueError("Dataset release identity mismatch")
    archive = manifest["archive"]
    if archive["path"] != "definitions.tar.gz" or not re.fullmatch(r"[0-9a-f]{64}", archive["sha256"]):
        raise ValueError("Invalid dataset archive binding")
    with urlopen(url + archive["path"], timeout=60) as response:
        raw = response.read(archive["size"] + 1)
    if len(raw) != archive["size"] or hashlib.sha256(raw).hexdigest() != archive["sha256"]:
        raise ValueError("Dataset archive hash or size mismatch")
    selected = {
        "repository": before["repository"], "revision": revision,
        "source_revision": manifest["source_revision"], "archive": archive,
        "datasets": manifest["datasets"],
        "files": {name: item for name, item in manifest["files"].items() if not name.startswith("data/")},
    }
    for option in ("dataset_sources", "default_dataset", "scorer"):
        if option in before:
            selected[option] = copy.deepcopy(before[option])
    previous = [{k: v for k, v in before.items() if k != "history"}, *before.get("history", [])]
    history = {item["revision"]: item for item in previous if item["revision"] != revision}
    selected["history"] = list(history.values())
    import_path = root / "evaluation" / ("lexbench-" + track + ".json")
    updates = {}
    with tempfile.TemporaryDirectory(prefix="browser-eval-dataset-pin-") as directory:
        unpack(raw, Path(directory), selected)
        if import_path.exists():
            imported = refresh_binding(json.loads(import_path.read_bytes()), revision, Path(directory) / "definitions")
            imported["kind"] = "huggingface_benchmark_binding"
            imported["repository"] = "https://huggingface.co/datasets/" + selected["repository"]
            imported["source_revision"] = selected["source_revision"]
            updates[import_path] = imported
    lock["datasets"][track] = selected
    updates[lock_path] = lock
    changes = binding_changes(before, selected)
    for path, value in updates.items():
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    validate_binding(root, "lexbench-" + track)
    return changes

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
    parser.add_argument('--track', required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    changes = pin(args.root, args.track, args.revision)
    print(f'Pinned {args.track} to {args.revision}; {len(changes)} binding changes')


if __name__ == '__main__':
    main()
