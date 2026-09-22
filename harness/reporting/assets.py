"""Materialize bound report assets from a Git-external source tree."""
import hashlib
import json
from pathlib import Path


def check_external_asset_boundary(root: Path) -> None:
    names = set()
    for manifest in (root / 'research').glob('*-content.json'):
        names.update(json.loads(manifest.read_text()))
    for profile in (root / 'evaluation').rglob('current-report.json'):
        names.update(json.loads(profile.read_text()).get('report_assets', {}))
    for directory in ('harness', 'research', 'evaluation'):
        for path in (root / directory).rglob('*'):
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            if any(relative == name or relative.endswith('/' + name) for name in names):
                raise ValueError(f'External report content duplicated in repository: {relative}')


def copy_bound_assets(source: Path, destination: Path, bindings: dict[str, str]) -> None:
    if not bindings:
        raise ValueError("report asset bindings are required")
    payloads = []
    for name, expected in bindings.items():
        relative = Path(name)
        if relative.is_absolute() or not relative.parts or '..' in relative.parts:
            raise ValueError("report asset path must be relative and contained")
        path = source / relative
        if any(parent.is_symlink() for parent in (path, *path.parents)):
            raise ValueError("report asset source must not contain symlinks")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError(f"report asset differs from binding: {name}")
        target = destination / relative
        if any(parent.is_symlink() for parent in (target, *target.parents)):
            raise ValueError("report asset destination must not contain symlinks")
        if target.exists():
            raise ValueError("report asset destination already exists")
        payloads.append((target, data))
    for target, data in payloads:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(data)
