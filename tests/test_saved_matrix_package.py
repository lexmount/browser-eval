"""Saved comparisons stay diagnostic: the Web Fetch release gate refuses them."""
import pytest

from harness.web_fetch.report_package import build as package
from tests.test_saved_matrix_evidence import build, fixture


def test_saved_matrix_cannot_be_packaged_for_release(tmp_path):
    spec, _, _, _ = fixture(tmp_path)
    output = build(tmp_path, spec).parent
    with pytest.raises(ValueError, match='Diagnostic evidence cannot enter certified assembly or reports'):
        package(output, tmp_path / 'report.zip')
    assert not (tmp_path / 'report.zip').exists()
