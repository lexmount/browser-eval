import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

import pytest

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

    def test_documentation_cannot_hide_a_copy_of_bound_report_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / 'research').mkdir()
            (root / 'research/example-content.json').write_text('{"figure.png": "hash"}')
            (root / 'docs').mkdir()
            (root / 'docs/figure.png').write_bytes(b'report figure')
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

    def test_unbound_retired_report_assets_are_also_outside_the_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / 'report-assets').mkdir()
            (root / 'report-assets/retired-figure.png').write_bytes(b'historical figure')
            with self.assertRaisesRegex(ValueError, 'outside'):
                check_external_asset_boundary(root)

    def test_report_assets_directory_symlinks_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / 'external-assets').mkdir()
            (root / 'report-assets').symlink_to(root / 'external-assets', target_is_directory=True)
            with self.assertRaisesRegex(ValueError, '[Ss]ymlink'):
                check_external_asset_boundary(root)

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


@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS system directory aliases')
@pytest.mark.parametrize('alias', ['/tmp', '/var'])
@pytest.mark.parametrize('side', ['source', 'destination', 'both'])
def test_bound_copy_accepts_system_aliases_without_changing_bytes(alias, side):
    directory = '/private/tmp' if alias == '/tmp' else str(Path(tempfile.gettempdir()).resolve())
    with tempfile.TemporaryDirectory(dir=directory) as work:
        root = Path(work).resolve()
        source, destination = root / 'source', root / 'destination'
        source.mkdir()
        (source / 'nested').mkdir()
        payload = b'Frozen reference bytes\n'
        (source / 'nested/reference.txt').write_bytes(payload)
        alias_root = Path(alias) / root.relative_to(Path('/private') / Path(alias).name)
        selected_source = alias_root / 'source' if side in {'source', 'both'} else source
        selected_destination = alias_root / 'destination' if side in {'destination', 'both'} else destination
        copy_bound_assets(selected_source, selected_destination,
                          {'nested/reference.txt': hashlib.sha256(payload).hexdigest()})
        assert (destination / 'nested/reference.txt').read_bytes() == payload
        assert (source / 'nested/reference.txt').read_bytes() == payload


@pytest.mark.parametrize('side', ['source', 'destination'])
def test_bound_copy_rejects_user_created_parent_links_before_writes(tmp_path, side):
    source = tmp_path / 'source'
    source.mkdir()
    payload = b'Frozen reference bytes'
    (source / 'reference.txt').write_bytes(payload)
    destination = tmp_path / 'destination'
    link = tmp_path / 'user-link'
    link.symlink_to(tmp_path, target_is_directory=True)
    selected_source = link / 'source' if side == 'source' else source
    selected_destination = link / 'destination' if side == 'destination' else destination
    with pytest.raises(ValueError, match='symlink'):
        copy_bound_assets(selected_source, selected_destination,
                          {'reference.txt': hashlib.sha256(payload).hexdigest()})
    assert not destination.exists()
    assert (source / 'reference.txt').read_bytes() == payload


def test_selected_json_assets_are_optional_bound_and_external(tmp_path):
    from harness.reporting.assets import bound_json_asset
    assert bound_json_asset(None, root=tmp_path) == (None, {})
    source = tmp_path / 'instance-history.json'
    source.write_text('{"releases": []}')
    binding = {'path': str(source), 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
    assert bound_json_asset(binding, root=tmp_path)[1] == {'releases': []}
    assert bound_json_asset({**binding, 'path': source.name}, root=tmp_path)[1] == {'releases': []}
    source.write_text('{"releases": ["unrelated report"]}')
    with pytest.raises(ValueError, match='differs'):
        bound_json_asset(binding, root=tmp_path)
    alias = tmp_path/'alias.json'
    alias.symlink_to(source)
    with pytest.raises(ValueError, match='[Ss]ymlink'):
        bound_json_asset({**binding, 'path': str(alias)}, root=tmp_path)
