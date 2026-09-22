import hashlib
import tempfile
import unittest
from pathlib import Path

from harness.reporting.assets import check_external_asset_boundary, copy_bound_assets


class ReportAssetsTest(unittest.TestCase):
    def test_repository_has_no_copies_of_bound_external_content(self):
        check_external_asset_boundary(Path(__file__).resolve().parents[1])

    def test_boundary_rejects_reintroduced_content_without_banning_common_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / 'research').mkdir()
            (root / 'research/example-content.json').write_text('{"figure.png": "hash"}')
            (root / 'research/methodology.png').write_bytes(b'common asset')
            check_external_asset_boundary(root)
            (root / 'research/figure.png').write_bytes(b'duplicate report asset')
            with self.assertRaisesRegex(ValueError, 'duplicated'):
                check_external_asset_boundary(root)

    def test_only_bound_assets_are_copied_byte_exactly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            source = root / 'source'
            source.mkdir()
            (source / 'image.png').write_bytes(b'public image')
            (source / 'private.log').write_text('must not be published')
            bindings = {'image.png': hashlib.sha256(b'public image').hexdigest()}
            copy_bound_assets(source, root / 'output', bindings)
            self.assertEqual((root / 'output/image.png').read_bytes(), b'public image')
            self.assertFalse((root / 'output/private.log').exists())
            with self.assertRaises(ValueError):
                copy_bound_assets(source, root / 'output', bindings)

    def test_changed_missing_unsafe_and_symlink_assets_reject_before_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            source = root / 'source'
            source.mkdir()
            (source / 'good').write_bytes(b'good')
            (source / 'link').symlink_to(source / 'good')
            digest = hashlib.sha256(b'good').hexdigest()
            for bad in ('missing', 'link', '../source/good', str(source / 'good'), 'good'):
                output = root / 'output'
                bindings = {'good': digest, bad: '0' * 64}
                with self.subTest(bad=bad), self.assertRaises((ValueError, FileNotFoundError)):
                    copy_bound_assets(source, output, bindings)
                self.assertFalse(output.exists())
