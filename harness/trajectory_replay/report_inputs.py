"""Locked benchmark definitions and profile-bound historical replay inputs."""
import hashlib
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
