"""Materialize bound report assets from a Git-external source tree."""
import hashlib
import json
import os
import re
import tempfile
import zipfile
from contextlib import ExitStack
from pathlib import Path

from harness.web_fetch.safety import contained_path, unlinked_path


def authored_main(raw: bytes) -> str:
    """Return the exact authored body, rejecting absent or ambiguous main elements."""
    bodies = re.findall(r'<main\b[^>]*>(.*?)</main>', raw.decode('utf-8'), re.S)
    if len(bodies) != 1:
        raise ValueError('Authored source must contain one main element')
    return bodies[0]


def restore_bound_content(export, binding, destination):
    """Recover declared authored fields from an immutable, verified export."""
    from harness.reporting.packs import read_packed_file

    source = binding['source']
    if source['recovery']['kind'] != 'html-main-json-v1':
        raise ValueError('Unsupported content recovery contract')
    container = source['recovery'].get('container', 'packed')
    if container not in {'packed', 'files'}:
        raise ValueError('Unsupported content source container')
    content = {'schema_version': 1}
    with ExitStack() as stack:
        export = Path(export)
        if container == 'files' and export.is_dir():
            def read_file(name):
                return contained_path(export, name, reject_symlinks=True).read_bytes()
        else:
            archive = stack.enter_context(zipfile.ZipFile(export))
            if len(archive.namelist()) != len(set(archive.namelist())):
                raise ValueError('Export contains duplicate file names')
            if container == 'packed':
                # ZIP timestamps may change while every virtual file stays fixed.
                manifest = archive.read('manifest.json')
                if hashlib.sha256(manifest).hexdigest() != source['manifest_sha256']:
                    raise ValueError('Export manifest differs from the pinned content source')
                def read_file(name):
                    return read_packed_file(archive, name)
            else:
                read_file = archive.read
        for field, editions in source['recovery']['fields'].items():
            content[field] = {}
            for language, name in editions.items():
                raw = read_file(name)
                if hashlib.sha256(raw).hexdigest() != source['files_sha256'][name]:
                    raise ValueError('Authored source differs from the pinned file')
                content[field][language] = authored_main(raw)
    payload = (json.dumps(content, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    if hashlib.sha256(payload).hexdigest() != binding['sha256']:
        raise ValueError('Recovered content differs from binding')
    destination = unlinked_path(Path(destination))
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
        os.link(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination


def restore_main():
    import argparse

    parser = argparse.ArgumentParser(description='Restore authored content from a pinned export')
    parser.add_argument('--binding', type=Path, required=True)
    parser.add_argument('--export', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    binding = json.loads(args.binding.read_text(encoding='utf-8'))
    print(restore_bound_content(args.export, binding, args.output))


def load_bound_content(source, binding, *, evidence_root=None, authority_root=None):
    """Read external authored data only for its declared evidence and authority."""
    if binding.get('schema_version') != 1:
        raise ValueError('Unsupported report content binding')
    for root, field in ((evidence_root, 'evidence'), (authority_root, 'authority')):
        declared = binding.get(field, {})
        if root is not None and not declared:
            raise ValueError('Report content lacks ' + field + ' bindings')
        if declared and root is None:
            raise ValueError('Report content requires an explicit ' + field + ' root')
        for name, expected in declared.items():
            path = contained_path(Path(root), name, reject_symlinks=True)
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError('Report content ' + field + ' differs: ' + name)
    path = contained_path(Path(source), binding['relative_path'], reject_symlinks=True)
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != binding['sha256']:
        raise ValueError('Report content differs from binding')
    content = json.loads(raw)
    if not isinstance(content, dict):
        raise ValueError('Report content must be an object')
    for field, editions in binding.get('rendered_sha256', {}).items():
        for language, expected in editions.items():
            body = content.get(field, {}).get(language)
            if not isinstance(body, str) or hashlib.sha256(body.encode()).hexdigest() != expected:
                raise ValueError('Report content rendered field differs: ' + field + '/' + language)
    return content


def check_external_asset_boundary(root: Path) -> None:
    legacy = root / 'report-assets'
    if legacy.is_symlink():
        raise ValueError('Report asset directory must not be a symlink')
    for path in legacy.rglob('*'):
        if path.is_symlink():
            raise ValueError('Report assets must not contain symlinks')
        if path.is_file():
            raise ValueError('Report-specific assets belong outside the repository: '
                             + path.relative_to(root).as_posix())
    names = set()
    for manifest in (root / 'research').glob('*-content.json'):
        names.update(json.loads(manifest.read_text()))
    for profile in (root / 'evaluation').rglob('current-report.json'):
        names.update(json.loads(profile.read_text()).get('report_assets', {}))
    for directory in ('harness', 'research', 'evaluation', 'docs', 'examples', 'scripts', 'tests'):
        for path in (root / directory).rglob('*'):
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            if any(relative == name or relative.endswith('/' + name) for name in names):
                raise ValueError(f'External report content duplicated in repository: {relative}')


def copy_bound_assets(source: Path, destination: Path, bindings: dict[str, str]) -> None:
    if not bindings:
        raise ValueError("report asset bindings are required")
    source = unlinked_path(source)
    destination = unlinked_path(destination)
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
