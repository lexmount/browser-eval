"""Locked benchmark definitions and profile-bound historical replay inputs."""
import hashlib
import json
from importlib.resources import files
from pathlib import Path
from urllib.parse import unquote, urlsplit

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


def replay_suite_for_hash(digest: str, *, family: str, dataset: dict | None = None) -> Path:
    """Recover verified suite bytes, preferring the edition saved with the run."""
    selected = replay_suite_binding(
        digest, family=family,
        revision=dataset.get('revision') if dataset is not None else None,
        definition=dataset.get('definition') if dataset is not None else None,
    )
    if dataset is not None and selected != dataset:
        raise ValueError('Replay suite dataset binding differs from the run input hash')
    if selected is None:
        raise ValueError(
            'historical suite is outside the locked definitions; supply its explicit path'
        )
    path = replay_input_path(selected, current='curated' if family == 'curated' else 'human')
    return verified_dataset_path(path, digest)


def replay_fixture_root(suite_path: Path | None = None) -> Path:
    """Use packaged pages for locked snapshots, adjacent pages for explicit suites."""
    if suite_path is not None:
        from harness.benchmarks.data import binding, cache_home
        suite_path = unlinked_path(suite_path)
        cache = cache_home()
        if suite_path.is_relative_to(cache):
            parts = suite_path.relative_to(cache).parts
            if len(parts) >= 4 and parts[0] in {'trajectory-replay', 'webarena-replay'}:
                track, revision = parts[:2]
                relative = '/'.join(parts[2:])
                selected = binding(track, revision)
                if relative in selected['files']:
                    verified_dataset_path(suite_path, selected['files'][relative]['sha256'])
                    return replay_fixture_root()
        return unlinked_path(suite_path.parent / 'fixtures')
    return Path(str(files("harness.trajectory_replay").joinpath("resources", "fixtures")))


def replay_fixture_hashes(root: Path) -> dict[str, str]:
    """Bind every file served, including historical nested pages and resources."""
    root = unlinked_path(root)
    if not root.is_dir():
        raise ValueError("Replay fixture directory is missing")
    result = {}
    for path in sorted(root.rglob('*')):
        path = unlinked_path(path)
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError("Replay fixture must be a regular file")
        result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    if not result:
        raise ValueError("Replay fixtures are empty")
    return result


def replay_fixture_inputs(suite_path: Path, calls) -> tuple[Path | None, dict[str, str]]:
    """Select and validate local inputs before browser or container initialization."""
    calls = tuple(calls)
    if not any('{fixture_origin}' in str(value) for call in calls for value in call.values()):
        return None, {}
    root = replay_fixture_root(suite_path)
    hashes = replay_fixture_hashes(root)
    for call in calls:
        url = call.get('url')
        if isinstance(url, str) and url.startswith('{fixture_origin}'):
            relative = unquote(urlsplit(url.replace('{fixture_origin}', 'http://fixture.test')).path)
            relative = relative.lstrip('/')
            if '..' in Path(relative).parts or '\\' in relative:
                raise ValueError("Unsafe replay fixture URL")
            target = unlinked_path(root / relative)
            if not target.exists():
                raise ValueError("Referenced replay fixture is missing: " + relative)
    return root, hashes


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
    digest: str, *, family: str, revision: str | None = None, definition: str | None = None
) -> dict | None:
    """Locate exact suite bytes in recorded HF editions without using a mutable default."""
    from harness.benchmarks.data import binding
    keys = ('curated',) if family == 'curated' else ('human', 'human-candidates', 'human-example')
    track = _CURRENT_DEFINITIONS[keys[0]][0]
    selected = binding(track, revision) if revision else binding(track)
    for edition in (selected, *selected.get('history', [])):
        for key in keys:
            _, relative = _CURRENT_DEFINITIONS[key]
            if definition is not None and relative != definition:
                continue
            expected = edition['files'].get('definitions/' + relative, {})
            if expected.get('sha256') == digest:
                return {'benchmark_dataset': track, 'definition': relative,
                        'revision': edition['revision']}
    return None
