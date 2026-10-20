"""Deterministic byte packs shared by static report publishers.

Adapters own routes, privacy projection and MIME metadata. This layer only stores
ordered byte blocks and returns byte-exact locations for their existing loaders.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import tempfile
from collections.abc import Iterable
from pathlib import Path


class ByteBlockStore:
    """Deduplicate ordered blocks without retaining the report payload in RAM."""
    def __init__(self):
        self.stream = tempfile.TemporaryFile()
        self.index = {}
        self.end = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.stream.close()

    def add(self, key, data):
        if key not in self.index:
            self.stream.seek(self.end)
            self.stream.write(data)
            self.index[key] = (self.end, len(data))
            self.end += len(data)

    def __getitem__(self, key):
        offset, size = self.index[key]
        self.stream.seek(offset)
        data = self.stream.read(size)
        if len(data) != size:
            raise ValueError("Truncated temporary report block")
        return data

    def items(self):
        for key in self.index:
            yield key, self[key]


def read_packed_file(archive, name, *, cache=None):
    """Read and verify one virtual file from either maintained block encoding."""
    manifest = read_packed_manifest(archive.read)
    cache = {} if cache is None else cache
    def pack(name):
        if name not in cache:
            cache[name] = gzip.decompress(archive.read(name))
        return cache[name]
    return reconstruct_packed_file(manifest,name,pack)


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



def write_compact_packs(entries, destination, *, architecture=None, pack_limit=16*1024*1024):
    """Content-defined deduplication with a compact, hash-bound file catalog.

    File hashes remain authoritative. Catalog IDs address immutable byte ranges;
    they do not duplicate millions of individual block hashes in the browser.
    """
    from array import array
    from fastcdc import fastcdc
    import sys

    destination.mkdir(parents=True, exist_ok=True)
    ids, files, packs, starts = {}, {}, [], []
    sizes, refs = array('H'), array('I')
    payload = bytearray()
    pack_start = 0

    def flush():
        nonlocal payload, pack_start
        if not payload:
            return
        name = f'data/{len(packs):03d}.gz'
        path = destination/name
        path.parent.mkdir(exist_ok=True)
        with path.open('xb') as stream:
            stream.write(gzip.compress(payload, compresslevel=9, mtime=0))
        packs.append(name)
        starts.append(pack_start)
        pack_start = len(sizes)
        payload = bytearray()

    for index, (name, metadata, raw) in enumerate(entries, 1):
        if len(raw) != metadata['size'] or hashlib.sha256(raw).hexdigest() != metadata['sha256']:
            raise ValueError('Cached public file differs from its hash: '+name)
        first, previous = len(refs), 0
        for chunk in fastcdc(raw, min_size=64, avg_size=256, max_size=4096, fat=True) if raw else ():
            key = hashlib.sha256(chunk.data).digest()
            block_id = ids.get(key)
            if block_id is None:
                if payload and len(payload)+chunk.length > pack_limit:
                    flush()
                block_id = len(sizes)
                ids[key] = block_id
                sizes.append(chunk.length)
                payload.extend(chunk.data)
            delta = block_id-previous
            refs.append((delta << 1) ^ (delta >> 31))
            previous = block_id
        files[name] = {**metadata, 'parts':[first, len(refs)-first]}
        if index % 1000 == 0:
            print(f'Compacted {index} cached public files', flush=True)
    flush()
    if sys.byteorder != 'little':
        sizes.byteswap()
        refs.byteswap()
    catalog = gzip.compress(sizes.tobytes()+refs.tobytes(), compresslevel=9, mtime=0)
    (destination/'catalog.bin.gz').write_bytes(catalog)
    wire = {'schema_version':3, 'files':files, 'packs':packs, 'pack_starts':starts,
            'block_count':len(sizes), 'architecture':{
                **(architecture or {}), 'content_packs':len(packs),
                'chunking':'fastcdc-1.7.0', 'chunk_min_bytes':64,
                'chunk_average_bytes':256, 'chunk_max_bytes':4096}}
    wire['architecture'].pop('block_bytes', None)
    raw = gzip.compress(json.dumps(wire,separators=(',',':')).encode(), compresslevel=9,mtime=0)
    (destination/'manifest.json.gz').write_bytes(raw)
    descriptor = {'schema_version':3, 'compressed_manifest':'manifest.json.gz',
                  'manifest_sha256':hashlib.sha256(raw).hexdigest()}
    (destination/'manifest.json').write_text(json.dumps(descriptor,separators=(',',':')))
    return wire


class _CompactParts:
    def __init__(self, refs, start, count, blocks):
        self.refs, self.start, self.count, self.blocks = refs, start, count, blocks

    def __iter__(self):
        value = 0
        for i in range(self.start, self.start+self.count):
            encoded = self.refs[i]
            value += (encoded >> 1) ^ -(encoded & 1)
            if not 0 <= value < self.blocks:
                raise ValueError('Compact catalog references an absent block')
            yield value

    def __len__(self):
        return self.count


class _CompactBlocks:
    def __init__(self, sizes, packs, starts):
        from array import array
        self.sizes, self.packs, self.starts = sizes, packs, starts
        self.offsets = array('I')
        offset, pack = 0, 0
        for i, size in enumerate(sizes):
            if pack+1 < len(starts) and i == starts[pack+1]:
                offset, pack = 0, pack+1
            if not size:
                raise ValueError('Compact catalog contains an empty block')
            self.offsets.append(offset)
            offset += size

    def __iter__(self):
        return iter(range(len(self.sizes)))

    def __len__(self):
        return len(self.sizes)

    def __getitem__(self, key):
        from bisect import bisect_right
        if type(key) is not int or not 0 <= key < len(self):
            raise KeyError(key)
        pack = bisect_right(self.starts, key)-1
        return {'pack':self.packs[pack], 'offset':self.offsets[key], 'size':self.sizes[key]}

    def values(self):
        return (self[key] for key in self)

    def for_pack(self, name):
        pack = self.packs.index(name)
        end = self.starts[pack+1] if pack+1 < len(self.starts) else len(self)
        return (self[key] for key in range(self.starts[pack], end))


def read_packed_manifest(read):
    """Normalize both maintained wire encodings without expanding the catalog."""
    from array import array
    import sys
    manifest = json.loads(read('manifest.json'))
    if manifest.get('schema_version') == 2:
        return manifest
    if manifest.get('schema_version') != 3 or manifest.get('compressed_manifest') != 'manifest.json.gz':
        raise ValueError('Unsupported virtual-file archive')
    encoded = read('manifest.json.gz')
    if hashlib.sha256(encoded).hexdigest() != manifest.get('manifest_sha256'):
        raise ValueError('Compact manifest differs from its hash')
    manifest = json.loads(gzip.decompress(encoded))
    count, starts, packs = manifest['block_count'], manifest['pack_starts'], manifest['packs']
    if (type(count) is not int or count <= 0 or len(starts) != len(packs)
        or not starts or starts[0] != 0 or starts[-1] >= count
        or any(type(x) is not int for x in starts)
        or any(a >= b for a,b in zip(starts, starts[1:]))
        or len(set(packs)) != len(packs)):
        raise ValueError('Invalid compact pack inventory')
    raw = gzip.decompress(read('catalog.bin.gz'))
    if len(raw) < count*2 or (len(raw)-count*2) % 4:
        raise ValueError('Invalid compact binary catalog length')
    sizes, refs = array('H'), array('I')
    sizes.frombytes(raw[:count*2])
    refs.frombytes(raw[count*2:])
    if sys.byteorder != 'little':
        sizes.byteswap()
        refs.byteswap()
    catalog = _CompactBlocks(sizes,packs,starts)
    ranges = []
    for entry in manifest['files'].values():
        start, length = entry['parts']
        if (type(start) is not int or type(length) is not int
            or min(start,length) < 0 or start+length > len(refs)):
            raise ValueError('Invalid compact file catalog range')
        ranges.append((start,length))
        entry['parts'] = _CompactParts(refs,start,length,count)
    cursor = 0
    for start,length in sorted(ranges):
        if start != cursor:
            raise ValueError('Compact catalog has unused or overlapping file references')
        cursor += length
    if cursor != len(refs):
        raise ValueError('Compact catalog has trailing file references')
    manifest['blocks'] = catalog
    return manifest


def reconstruct_packed_file(manifest,name,pack):
    import base64
    entry = manifest['files'][name]
    if 'inline_base64' in entry:
        raw = base64.b64decode(entry['inline_base64'],validate=True)
    else:
        chunks = []
        for key in entry['parts']:
            block = manifest['blocks'][key]
            data = pack(block['pack'])[block['offset']:block['offset']+block['size']]
            if len(data) != block['size'] or (manifest['schema_version'] == 2
                and hashlib.sha256(data).hexdigest() != key):
                raise ValueError('Virtual-file block differs from its hash')
            chunks.append(data)
        raw = b''.join(chunks)
    if len(raw) != entry['size'] or hashlib.sha256(raw).hexdigest() != entry['sha256']:
        raise ValueError('Virtual file differs from its hash: '+name)
    return raw


class DecompressedPackCache:
    """Decompress each pack once; bound mapped handles and Python heap memory.

    Returned maps are borrowed until another pack is requested. Callers copy
    the selected byte range immediately, rather than retain mapped views.
    """
    def __init__(self, read, *, max_maps=16):
        from collections import OrderedDict
        if max_maps < 1:
            raise ValueError('Mapped pack cache must have a positive capacity')
        self.read, self.max_maps = read, max_maps
        self.paths, self.maps = {}, OrderedDict()

    def __enter__(self):
        self.directory = tempfile.TemporaryDirectory(prefix='report-pack-cache-')
        return self

    def __call__(self, name):
        import mmap
        if name in self.maps:
            self.maps.move_to_end(name)
            return self.maps[name]
        if name not in self.paths:
            path = Path(self.directory.name)/str(len(self.paths))
            path.write_bytes(gzip.decompress(self.read(name)))
            self.paths[name] = path
        path = self.paths[name]
        if not path.stat().st_size:
            return b''
        if len(self.maps) >= self.max_maps:
            _,old = self.maps.popitem(last=False)
            old.close()
        with path.open('rb') as stream:
            view = mmap.mmap(stream.fileno(),0,access=mmap.ACCESS_READ)
        self.maps[name] = view
        return view

    def __exit__(self,*_):
        for view in self.maps.values():
            view.close()
        self.maps.clear()
        self.directory.cleanup()
