"""Deterministic byte packs shared by static report publishers.

Adapters own routes, privacy projection and MIME metadata. This layer only stores
ordered byte blocks and returns byte-exact locations for their existing loaders.
"""
from __future__ import annotations

import gzip
import hashlib
import json
from collections.abc import Iterable
from pathlib import Path


def read_packed_file(archive, name):
    """Read and verify one virtual file from a schema-v2 block archive."""
    manifest = json.loads(archive.read('manifest.json'))
    if manifest.get('schema_version') != 2:
        raise ValueError('Unsupported virtual-file archive')
    entry = manifest['files'][name]
    cache, chunks = {}, []
    for key in entry['parts']:
        block = manifest['blocks'][key]
        pack = block['pack']
        if pack not in cache:
            cache[pack] = gzip.decompress(archive.read(pack))
        raw = cache[pack][block['offset']:block['offset'] + block['size']]
        if len(raw) != block['size'] or hashlib.sha256(raw).hexdigest() != key:
            raise ValueError('Virtual-file block differs from its hash')
        chunks.append(raw)
    raw = b''.join(chunks)
    if len(raw) != entry['size'] or hashlib.sha256(raw).hexdigest() != entry['sha256']:
        raise ValueError('Virtual file differs from its hash')
    return raw


def write_packs(
    blocks: Iterable[tuple[str, bytes]],
    destination: Path,
    *,
    limit: int,
    pattern: str,
    compresslevel: int,
) -> tuple[dict[str, dict], int]:
    if limit <= 0:
        raise ValueError("pack limit must be positive")
    catalog: dict[str, dict] = {}
    payload = bytearray()
    pending: list[str] = []
    index = 0

    def flush():
        nonlocal index, payload, pending
        if not pending:
            return
        relative = Path(pattern.format(index=index))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("pack path must stay inside the destination")
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        # Refuse accidental replacement if a naming pattern repeats.
        with path.open("xb") as stream:
            stream.write(gzip.compress(bytes(payload), compresslevel=compresslevel, mtime=0))
        for key in pending:
            catalog[key] = {"pack": relative.as_posix(), **catalog[key]}
        index += 1
        payload = bytearray()
        pending = []

    for key, data in blocks:
        if key in catalog:
            raise ValueError(f"duplicate block: {key}")
        if pending and len(payload) + len(data) > limit:
            flush()
        catalog[key] = {"offset": len(payload), "size": len(data)}
        payload.extend(data)
        pending.append(key)
    flush()
    return catalog, index
