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
