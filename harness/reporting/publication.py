"""Stage release archives from frozen inputs before installing their proof."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import stat
import tempfile
import urllib.parse
import uuid
import zipfile
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from urllib.error import HTTPError

from lexbench_webfetch.paths import contained_path

from harness.web_fetch.safety import evidence_tree_identity, reject_evidence_symlinks


@dataclass(frozen=True)
class PublicationSnapshot:
    archive: Path
    sha256: str
    sources: dict
    signatures: dict = field(default_factory=dict)

    def check(self) -> None:
        for path, value in {**self.sources, self.archive: self.sha256}.items():
            signature = _file_signature(path)
            # Windows ctime is creation time, so retain byte checks there.
            if os.name != 'nt' and self.signatures.get(path) == signature:
                continue
            if _identity(path) != value or _file_signature(path) != signature:
                raise ValueError('publication inputs changed after validation')
            self.signatures[path] = signature

    def verify(self) -> None:
        """Rehash the complete bytes at the final publication boundary."""
        for path, value in {**self.sources, self.archive: self.sha256}.items():
            if _identity(path) != value:
                raise ValueError('publication inputs changed after validation')


def _file_signature(path: Path):
    """Kernel change times prevent same-size writes or restored mtimes hiding edits."""
    entries = {}

    def visit(current: Path, relative: str):
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise ValueError('Publication inputs contain a symlink')
        entries[relative] = (info.st_dev, info.st_ino, info.st_mode,
                             info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if stat.S_ISDIR(info.st_mode):
            with os.scandir(current) as children:
                for child in children:
                    visit(Path(child.path), relative + '/' + child.name)
        elif not stat.S_ISREG(info.st_mode):
            raise ValueError('Publication inputs must be regular files or directories')

    visit(path, '')
    return entries


@contextmanager
def snapshot_publication(archive: Path, *, inputs: Sequence[Path]):
    """Upload a private byte-exact copy while guarding the release authority."""
    paths = dict.fromkeys((archive, *inputs))
    signatures = {path: _file_signature(path) for path in paths}
    sources = {path: _identity(path) for path in paths}
    if any(_file_signature(path) != signatures[path] for path in paths):
        raise ValueError('publication inputs changed during snapshot creation')
    with tempfile.TemporaryDirectory(prefix='publication-snapshot-') as work:
        frozen = Path(work) / 'archive.zip'
        shutil.copyfile(archive, frozen)
        snapshot = PublicationSnapshot(frozen, sources[archive], sources, signatures)
        snapshot.check()
        yield snapshot


def archive_inventory(source: Path | bytes) -> dict[str, str]:
    """Compare exported ZIPs without accepting duplicate paths or extracting files."""
    with zipfile.ZipFile(io.BytesIO(source) if isinstance(source, bytes) else source) as archive:
        members = [item for item in archive.infolist() if not item.is_dir()]
        names = [item.filename for item in members]
        if not names or len(names) != len(set(names)):
            raise ValueError('publication ZIP requires nonempty unique file paths')
        return {item.filename: hashlib.sha256(archive.read(item)).hexdigest() for item in members}


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
    excluded: Sequence[str] = (),
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
    complete = before[report]
    if not isinstance(complete, dict):
        raise ValueError('publication source must be a directory')
    inventory = {name: digest for name, digest in complete.items() if name not in excluded}
    if (not inventory
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


def public_share_context(base: str, share_url: str) -> tuple[str, str]:
    target = urllib.parse.urlsplit(share_url)
    authority = urllib.parse.urlsplit(base)
    if (
        target.scheme != authority.scheme
        or target.netloc != authority.netloc
        or not target.path.startswith("/v/")
        or target.query
        or target.fragment
    ):
        raise ValueError("publication has no valid same-origin public share URL")
    token = target.path.removeprefix("/v/")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", token):
        raise ValueError("publication has no valid public share token")
    return target.path, token


def json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()


def atomic_write(path: Path, payload: bytes) -> None:
    _destination(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        _destination(path)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def recover_binding(
    profile: Path, attestation: Path, journal: Path, *, schema: str,
    legacy_schema: str | None = None, writer: Callable = atomic_write,
) -> None:
    for path in (profile, attestation, journal):
        _destination(path)
    if not journal.exists():
        return
    if len({path.resolve() for path in (profile, attestation, journal)}) != 3:
        raise ValueError('publication binding paths must be distinct')
    pending_bytes = journal.read_bytes()
    pending = json.loads(pending_bytes)
    if not isinstance(pending, dict):
        raise ValueError('invalid pending publication binding')
    target_profile = pending.get('profile')
    target_proof = pending.get('attestation')
    if not isinstance(target_profile, dict) or not isinstance(target_proof, dict):
        raise ValueError('invalid pending publication binding payload')
    payloads = {'profile': json_bytes(target_profile), 'attestation': json_bytes(target_proof)}
    desired = {key: hashlib.sha256(data).hexdigest() for key, data in payloads.items()}
    if target_proof.get('profile_sha256') != desired['profile']:
        raise ValueError('pending publication binding has an invalid profile hash')
    targets = {'profile': profile.resolve(), 'attestation': attestation.resolve()}
    if pending.get('schema') == schema:
        if pending.get('targets') != {key: str(path) for key, path in targets.items()}:
            raise ValueError('pending publication binding targets differ from this authority')
        previous = pending.get('previous')
        if not isinstance(previous, dict) or set(previous) != set(targets) or any(
            not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value)
            for value in previous.values()
        ):
            raise ValueError('pending publication binding has invalid source hashes')
    elif legacy_schema is not None and pending.get('schema') == legacy_schema:
        # Old journals lacked before-hashes. They can safely finish proof installation
        # only once the intended profile is already present; never replace another profile.
        current = json.loads(attestation.read_bytes())
        if (_identity(profile) != desired['profile'] or not isinstance(current, dict)
                or {k: v for k, v in current.items() if k != 'profile_sha256'}
                != {k: v for k, v in target_proof.items() if k != 'profile_sha256'}):
            raise ValueError('legacy publication binding differs from current authority')
        previous = {key: _identity(path) for key, path in targets.items()}
    else:
        raise ValueError('invalid pending publication binding schema')

    def unchanged():
        _destination(journal)
        if journal.read_bytes() != pending_bytes:
            raise ValueError('pending publication binding changed during recovery')
        if targets != {'profile': profile.resolve(), 'attestation': attestation.resolve()}:
            raise ValueError('publication binding target changed during recovery')
        for key, path in targets.items():
            _destination(path)
            if _identity(path) not in (previous[key], desired[key]):
                raise ValueError('publication authority changed during binding recovery')

    unchanged()
    for key, path in targets.items():
        unchanged()
        if _identity(path) != desired[key]:
            writer(path, payloads[key])
    unchanged()
    if any(_identity(path) != desired[key] for key, path in targets.items()):
        raise ValueError('publication binding did not install the complete authority')
    journal.unlink()


def bind_publication_files(
    profile: Path, attestation: Path, journal: Path, *, publication: dict, schema: str,
    expected: dict | None = None, writer: Callable = atomic_write,
) -> None:
    for path in (profile, attestation, journal):
        _destination(path)
    if len({path.resolve() for path in (profile, attestation, journal)}) != 3:
        raise ValueError('publication binding paths must be distinct')
    if journal.exists():
        raise ValueError('recover the pending publication binding before a new binding')
    before = {path: _identity(path) for path in (profile, attestation)}
    if expected is not None and any(before[path] != expected.get(path) for path in before):
        raise ValueError('publication authority changed before binding')
    updated = json.loads(profile.read_bytes())
    proof = json.loads(attestation.read_bytes())
    if not isinstance(updated, dict) or not isinstance(proof, dict):
        raise ValueError('publication authority must contain JSON objects')
    if not isinstance(updated.get('publication', {}), dict):
        raise ValueError('invalid publication target')
    updated.setdefault('publication', {}).update(publication)
    proof['profile_sha256'] = hashlib.sha256(json_bytes(updated)).hexdigest()
    pending = {
        'schema': schema,
        'targets': {'profile': str(profile.resolve()), 'attestation': str(attestation.resolve())},
        'previous': {'profile': before[profile], 'attestation': before[attestation]},
        'profile': updated, 'attestation': proof,
    }
    if any(_identity(path) != digest for path, digest in before.items()):
        raise ValueError('publication authority changed while preparing binding')
    atomic_create(journal, json_bytes(pending))
    recover_binding(profile, attestation, journal, schema=schema, writer=writer)


def atomic_create(path: Path, payload: bytes) -> None:
    """Install a complete new journal without replacing another process's record."""
    _destination(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.' + path.name + '.', dir=path.parent) as work:
        staged = Path(work) / 'record'
        atomic_write(staged, payload)
        os.link(staged, path)


@dataclass(frozen=True)
class PublicationOperation:
    journal: Path
    prefix: str
    writer: Callable = atomic_create

    def pending(self, identity: dict, *, allow_create: bool) -> tuple[dict, bool]:
        _destination(self.journal)
        if self.journal.exists():
            pending = json.loads(self.journal.read_bytes())
            if not isinstance(pending, dict):
                raise ValueError('invalid pending Artifact Site operation')
            key = pending.pop('key', None)
            if (pending != identity or not isinstance(key, str)
                    or not re.fullmatch(r'[A-Za-z0-9._:-]{8,128}', key)):
                raise ValueError('pending Artifact Site operation differs from this release')
            return {**pending, 'key': key}, True
        if not allow_create:
            raise ValueError('no bound site or matching pending creation operation')
        pending = {**identity, 'key': self.prefix + uuid.uuid4().hex}
        try:
            self.writer(self.journal, json_bytes(pending))
        except FileExistsError:
            return self.pending(identity, allow_create=False)
        return pending, False

    def completed(self, key: str, *, request: Callable, decode: Callable):
        try:
            status, raw = request('/api/operations/' + urllib.parse.quote(key))
        except HTTPError as error:
            if error.code != 404:
                raise
            error.close()
            return None
        if status == 404:
            return None
        operation = decode(status, raw, operation='operation recovery')
        state = operation.get('status')
        if state == 'running':
            raise RuntimeError('Artifact Site operation is still running; retry this command later')
        if state == 'retryable':
            return None
        if state != 'completed':
            raise RuntimeError('Artifact Site operation returned an unknown recovery status')
        result, http_status = operation.get('result'), operation.get('httpStatus')
        if not isinstance(result, dict) or type(http_status) is not int:
            raise RuntimeError('completed Artifact Site operation omitted its result')
        return http_status, json.dumps(result, ensure_ascii=False).encode()

    def upload(
        self, path: str, payload: bytes, content_type: str, identity: dict, *,
        allow_create: bool, request: Callable, decode: Callable, guard: Callable = lambda: None,
    ) -> tuple[int, bytes]:
        guard()
        pending, resumed = self.pending(identity, allow_create=allow_create)
        if resumed:
            recovered = self.completed(pending['key'], request=request, decode=decode)
            if recovered is not None:
                return recovered
        guard()
        return request(path, payload, content_type,
                       extra_headers={'Idempotency-Key': pending['key']})

    def clear(self, identity: dict) -> None:
        _destination(self.journal)
        if self.journal.exists():
            self.pending(identity, allow_create=False)
            self.journal.unlink()


def binding_receipt_matches(
    path: Path, *, base: str, archive_sha256: str, slug: str, version: str,
    version_field: str = 'version',
) -> bool:
    _destination(path)
    if not path.exists():
        return False
    receipt = json.loads(path.read_bytes())
    return (isinstance(receipt, dict) and receipt.get('phase') == 'binding'
            and receipt.get('verified') is False and receipt.get('base_url') == base
            and receipt.get('archive_sha256') == archive_sha256 and receipt.get('slug') == slug
            and receipt.get(version_field) == version)
