"""Stage release archives from frozen inputs before installing their proof."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from collections.abc import Callable, Sequence
from pathlib import Path

from harness.web_fetch.safety import (
    contained_path,
    evidence_tree_identity,
    reject_evidence_symlinks,
)


def _identity(path: Path):
    reject_evidence_symlinks(path)
    if path.is_dir():
        return evidence_tree_identity(path)
    if not path.is_file():
        raise ValueError(f'missing publication input: {path}')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _destination(path: Path) -> None:
    if path.is_symlink():
        raise ValueError('publication destination must not be a symlink')
    if path.exists() and not path.is_file():
        raise ValueError('publication destination must be a regular file')


def package_archive(
    report: Path,
    output: Path,
    receipt: Path,
    *,
    inputs: Sequence[Path],
    validate: Callable[[], None],
    attest: Callable[[Path], dict],
    max_files: int | None = None,
    max_bytes: int | None = None,
) -> dict:
    """Failed gates, drift or installation retain the preceding local release.

    Archive and proof are individually renamed atomically. Their existing hash
    binding makes an interrupted two-file installation fail publisher validation.
    Handled installation errors restore both preceding files.
    """
    declared = {path: path.parent.resolve() for path in (output, receipt)}
    for path in declared:
        _destination(path)
        if any(path.resolve().is_relative_to(source.resolve()) for source in (report, *inputs)
               if source.is_dir()):
            raise ValueError('publication outputs must stay outside their inputs')
        if any(path.resolve() == source.resolve() for source in inputs):
            raise ValueError('publication output cannot replace an input')
    if output.resolve() == receipt.resolve():
        raise ValueError('archive and attestation must have distinct destinations')
    output = output.parent.resolve() / output.name
    receipt = receipt.parent.resolve() / receipt.name
    before = {path: _identity(path) for path in dict.fromkeys((report, *inputs))}
    inventory = before[report]
    if (not isinstance(inventory, dict) or not inventory
            or (max_files is not None and len(inventory) > max_files)):
        raise ValueError(f'publication file count is outside the permitted range: {max_files}')
    for name in inventory:
        contained_path(report, name, reject_symlinks=True)
        parts = Path(name).parts
        if any(part.startswith('.') or part in {'node_modules', '__MACOSX'} for part in parts):
            raise ValueError(f'unsafe publication path: {name}')
    destinations = {path: _identity(path) if path.exists() else None for path in (output, receipt)}

    def unchanged():
        if any(path.parent.resolve() != parent for path, parent in declared.items()):
            raise ValueError('publication destination parent changed during packaging')
        if any(_identity(path) != value for path, value in before.items()):
            raise ValueError('publication inputs changed during packaging')
        for path, value in destinations.items():
            _destination(path)
            if (_identity(path) if path.exists() else None) != value:
                raise ValueError('publication destination changed during packaging')

    validate()
    unchanged()
    output.parent.mkdir(parents=True, exist_ok=True)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    # Separate staging directories keep each final rename on its own filesystem.
    with tempfile.TemporaryDirectory(prefix='.release-', dir=output.parent) as archive_work, \
            tempfile.TemporaryDirectory(prefix='.proof-', dir=receipt.parent) as proof_work:
        staged = Path(archive_work) / 'archive.zip'
        proof = Path(proof_work) / 'attestation.json'
        with zipfile.ZipFile(staged, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name in sorted(inventory):
                archive.write(report / name, name)
        if max_bytes is not None and staged.stat().st_size > max_bytes:
            raise ValueError('publication package exceeds staged-upload limit')
        result = attest(staged)
        proof.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        unchanged()
        # Verify the archive itself, independently of a later source-tree hash.
        with zipfile.ZipFile(staged) as archive:
            if {name: hashlib.sha256(archive.read(name)).hexdigest()
                    for name in archive.namelist()} != inventory:
                raise ValueError('archive differs from validated report bytes')
        backups = {}
        for path, work in ((output, archive_work), (receipt, proof_work)):
            if path.exists():
                backup = Path(work) / 'previous'
                shutil.copyfile(path, backup)
                backups[path] = backup
        unchanged()
        installed = []
        try:
            for source, target in ((staged, output), (proof, receipt)):
                source.replace(target)
                installed.append(target)
        except BaseException:
            for target in reversed(installed):
                if target in backups:
                    try:
                        backups[target].replace(target)
                    except OSError as error:
                        descriptor, recovery = tempfile.mkstemp(
                            prefix='.' + target.name + '-recovery-', dir=target.parent
                        )
                        os.close(descriptor)
                        shutil.copyfile(backups[target], recovery)
                        raise RuntimeError(
                            f'publication rollback failed; original retained at {recovery}'
                        ) from error
                else:
                    target.unlink(missing_ok=True)
            raise
    return result
