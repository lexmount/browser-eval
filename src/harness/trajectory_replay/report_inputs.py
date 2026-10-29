"""Locked benchmark definitions and profile-bound historical replay inputs."""
import hashlib
import json
from importlib.resources import files
from pathlib import Path

from harness.web_fetch.safety import unlinked_path


def verified_dataset_path(path: Path, digest: str) -> Path:
    path = unlinked_path(path)
    if not path.is_file():
        raise ValueError("bound corrected dataset is missing or symlinked")
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError("bound corrected dataset changed")
    return path


def bound_dataset_path(profile: dict) -> Path:
    dataset = profile.get("dataset") or {}
    path_value = dataset.get("path")
    if dataset.get("benchmark_dataset"):
        path_value = replay_input_path(dataset, current='corrected-90')
    digest = dataset.get("sha256")
    if not isinstance(path_value, (str, Path)) or not path_value or not isinstance(digest, str):
        raise ValueError("replay report profile requires a bound corrected dataset")
    path = Path(path_value)
    return verified_dataset_path(path, digest)


_CURRENT_DEFINITIONS = {
    'curated': ('trajectory-replay', 'suites/curated-v1.json'),
    'human': ('webarena-replay', 'suites/human-qualified-v1.json'),
    'human-candidates': ('webarena-replay', 'inventories/human-candidates-v1.json'),
    'human-example': ('webarena-replay', 'historical/normalized-example-v1.json'),
    'human-traces': ('webarena-replay', 'provenance/human-trace-inventory-v1.json'),
    'human-exclusions': ('webarena-replay', 'annotations/human-evaluator-exclusions-v1.json'),
    'corrected-90': ('webarena-replay', 'canonical/corrected-90-v1.json'),
    'full-catalog': ('webarena-replay', 'catalog/full-812-v1.json'),
    'full-portable': ('webarena-replay', 'suites/full-portable-v1.json'),
}


def replay_input_path(value: str | Path | dict | None = None, *, current: str) -> Path:
    """Resolve current locked data lazily; explicit historical paths stay explicit."""
    if value is None or isinstance(value, dict):
        from harness.benchmarks.data import definition_path
        track, relative = _CURRENT_DEFINITIONS[current]
        if isinstance(value, dict):
            track = value['benchmark_dataset']
            relative = value['definition']
        return definition_path(
            track, relative, value.get("revision") if isinstance(value, dict) else None
        )
    return Path(value).expanduser()


def replay_suite_for_hash(digest: str, *, family: str) -> Path:
    """Recover a supported report's exact input bytes, never substitute a newer suite."""
    keys = ('curated',) if family == 'curated' else ('human', 'human-candidates', 'human-example')
    for key in keys:
        path = replay_input_path(current=key)
        if hashlib.sha256(path.read_bytes()).hexdigest() == digest:
            return path
    raise ValueError('historical suite is outside the locked definitions; supply its explicit path')


def replay_fixture_root() -> Path:
    """Installed, versioned local-page fixtures shared by both replay runners."""
    return Path(str(files("harness.trajectory_replay").joinpath("resources", "fixtures")))


def replay_fixture_hashes(root: Path) -> dict[str, str]:
    """Bind the packaged pages actually served, separately from the HF suite."""
    paths = sorted(root.glob("*.html"))
    if not paths:
        raise ValueError("Packaged replay fixtures are missing")
    return {path.name: hashlib.sha256(unlinked_path(path).read_bytes()).hexdigest()
            for path in paths}


def agent_smoke_input(path: str | Path | None) -> dict | None:
    """Attach only explicitly selected historical evidence, independently of the suite."""
    if path is None:
        return None
    source = unlinked_path(Path(path).expanduser())
    raw = source.read_bytes()
    data = json.loads(raw)
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise ValueError("Agent smoke evidence must contain a results list")
    return {
        **data,
        "provenance": {
            "source_file": source.name,
            "source_sha256": hashlib.sha256(raw).hexdigest(),
            "independent_of_browser_microbenchmark": True,
            "reused_not_rerun": True,
        },
    }


def replay_suite_binding(
    digest: str, *, family: str, revision: str | None = None
) -> dict | None:
    """Locate exact suite bytes in recorded HF editions without using a mutable default."""
    from harness.benchmarks.data import binding
    keys = ('curated',) if family == 'curated' else ('human', 'human-candidates', 'human-example')
    track = _CURRENT_DEFINITIONS[keys[0]][0]
    selected = binding(track, revision) if revision else binding(track)
    for edition in (selected, *selected.get('history', [])):
        for key in keys:
            _, relative = _CURRENT_DEFINITIONS[key]
            expected = edition['files'].get('definitions/' + relative, {})
            if expected.get('sha256') == digest:
                return {'benchmark_dataset': track, 'definition': relative,
                        'revision': edition['revision']}
    return None
