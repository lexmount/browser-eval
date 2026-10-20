"""Public byte blocks survive both report pack formats without loss or mutation."""
import gzip
import tempfile
import unittest
from pathlib import Path

from harness.reporting.packs import write_packs


class ReportPacksTests(unittest.TestCase):
    def test_roundtrip_and_determinism_for_both_public_formats(self):
        source = [('empty', b''), ('unicode', '中文'.encode()), ('binary', bytes(range(256))),
                  ('tail', b'end'), ('empty-tail', b'')]
        for pattern, level in [('data/{index:03d}.gz', 9), ('evidence-{index:02d}.bin.gz', 6)]:
            with self.subTest(pattern=pattern), tempfile.TemporaryDirectory() as temp:
                first, second = Path(temp) / 'first', Path(temp) / 'second'
                a, count = write_packs(source, first, limit=8, pattern=pattern, compresslevel=level)
                b, other_count = write_packs(source, second, limit=8, pattern=pattern,
                                            compresslevel=level)
                self.assertEqual((a, count), (b, other_count))
                self.assertGreater(count, 1)
                for key, expected in source:
                    entry = a[key]
                    self.assertEqual((first / entry['pack']).read_bytes(),
                                     (second / entry['pack']).read_bytes())
                    raw = gzip.decompress((first / entry['pack']).read_bytes())
                    self.assertEqual(raw[entry['offset']:entry['offset'] + entry['size']], expected)

    def test_empty_only_evidence_still_has_a_real_pack(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            entries, count = write_packs([('empty', b'')], root, limit=8,
                                        pattern='{index}.gz', compresslevel=6)
            self.assertEqual(count, 1)
            self.assertEqual(gzip.decompress((root / entries['empty']['pack']).read_bytes()), b'')

    def test_rejects_duplicate_keys_and_unsafe_pack_paths(self):
        for blocks, pattern in [([('x', b'a'), ('x', b'b')], '{index}.gz'),
                                ([('x', b'a')], '../{index}.gz')]:
            with self.subTest(pattern=pattern), tempfile.TemporaryDirectory() as temp:
                with self.assertRaises(ValueError):
                    write_packs(blocks, Path(temp), limit=8, pattern=pattern, compresslevel=6)


def test_disk_blocks_keep_deterministic_packs_and_bounded_memory(tmp_path):
    import hashlib
    import tracemalloc
    from harness.reporting.packs import ByteBlockStore
    keys = []
    tracemalloc.start()
    try:
        with ByteBlockStore() as blocks:
            for index in range(256):
                data = bytes([index]) * 65536
                key = hashlib.sha256(data).hexdigest()
                keys.append(key)
                blocks.add(key, data)
                blocks.add(key, data)
            _, peak = tracemalloc.get_traced_memory()
            assert peak < 2_000_000
            actual, count = write_packs(blocks.items(), tmp_path, limit=131072,
                                       pattern='{index}.gz', compresslevel=1)
            assert count == 128
            for index, key in enumerate(keys):
                entry = actual[key]
                raw = gzip.decompress((tmp_path / entry['pack']).read_bytes())
                assert raw[entry['offset']:entry['offset'] + entry['size']] == bytes([index]) * 65536
    finally:
        tracemalloc.stop()


def test_compact_catalog_deduplicates_shifted_files_and_verifies_downloads(tmp_path):
    import hashlib
    import random
    import zipfile
    from harness.reporting.packs import read_packed_manifest, read_packed_file, write_compact_packs
    raw = random.Random(19).randbytes(1_000_000)
    entries = [('empty.txt', {'size':0,'sha256':hashlib.sha256(b'').hexdigest()}, b'')]
    for i in range(8):
        data = bytes([i])*i+raw
        entries.append((str(i)+'.bin', {'size':len(data),'sha256':hashlib.sha256(data).hexdigest(),
                                      'source_sha256':'source-'+str(i)},data))
    wire = write_compact_packs(entries,tmp_path/'content',pack_limit=65536)
    assert len(wire['packs']) > 1
    archive = tmp_path/'report.zip'
    with zipfile.ZipFile(archive,'w') as bundle:
        for path in (tmp_path/'content').rglob('*'):
            if path.is_file():
                bundle.write(path,path.relative_to(tmp_path/'content'))
    with zipfile.ZipFile(archive) as bundle:
        manifest = read_packed_manifest(bundle.read)
        assert sum(block['size'] for block in manifest['blocks'].values()) < 1_100_000
        for name,metadata,data in entries:
            assert read_packed_file(bundle,name) == data
            assert manifest['files'][name].get('source_sha256') == metadata.get('source_sha256')


def test_compact_catalog_rejects_corrupt_ranges_blocks_and_file_bytes(tmp_path):
    import gzip
    import hashlib
    from harness.reporting.packs import read_packed_manifest, write_compact_packs
    import pytest
    data = b'public evidence '*300
    wire = write_compact_packs([('a.txt',{'size':len(data),'sha256':hashlib.sha256(data).hexdigest()},data)],tmp_path)
    original = {str(p.relative_to(tmp_path)):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    for kind in ('manifest_hash','short_catalog','trailing_refs','absent_block','file_hash'):
        files = dict(original)
        if kind == 'manifest_hash':
            files['manifest.json.gz'] += b'bad'
        elif kind in ('short_catalog','trailing_refs','absent_block'):
            catalog = gzip.decompress(files['catalog.bin.gz'])
            if kind == 'short_catalog':
                catalog = catalog[:-1]
            elif kind == 'trailing_refs':
                catalog += b'\0'*4
            else:
                offset = wire['block_count']*2
                catalog = catalog[:offset]+b'\xfe\xff\xff\xff'+catalog[offset+4:]
            files['catalog.bin.gz'] = gzip.compress(catalog)
        else:
            name = wire['packs'][0]
            raw = gzip.decompress(files[name])
            files[name] = gzip.compress(bytes([raw[0]^1])+raw[1:])
        with pytest.raises(ValueError):
            manifest = read_packed_manifest(files.__getitem__)
            entry = manifest['files']['a.txt']
            chunks = []
            for key in entry['parts']:
                block = manifest['blocks'][key]
                pack = gzip.decompress(files[block['pack']])
                chunks.append(pack[block['offset']:block['offset']+block['size']])
            actual = b''.join(chunks)
            if hashlib.sha256(actual).hexdigest() != entry['sha256']:
                raise ValueError('file differs')


def test_validation_pack_cache_decompresses_once_with_bounded_handles(tmp_path):
    from collections import Counter
    from harness.reporting.packs import DecompressedPackCache
    compressed = {str(i):gzip.compress(bytes([i])*1_000_000) for i in range(20)}
    reads = Counter()
    def read(name):
        reads[name] += 1
        return compressed[name]
    with DecompressedPackCache(read,max_maps=2) as cache:
        for _ in range(3):
            for index in range(20):
                actual = cache(str(index))
                assert actual[:100] == bytes([index])*100
                assert len(actual) == 1_000_000
                assert len(cache.maps) <= 2
        assert set(reads.values()) == {1}
