"""Package saved comparisons through the same privacy and integrity boundary."""
import json

import pytest

from harness.web_fetch.report_package import build as package, validate_release
from tests.test_saved_matrix_evidence import build, fixture


def test_saved_matrix_packages_verified_outputs(tmp_path):
    spec, _, _, _ = fixture(tmp_path)
    output = build(tmp_path, spec).parent
    archive = tmp_path / 'report.zip'
    package(output, archive)
    assert archive.is_file()


def test_saved_matrix_rejects_tampered_output_before_packaging(tmp_path):
    spec, _, _, _ = fixture(tmp_path)
    output = build(tmp_path, spec).parent
    matrix = json.loads((output / 'matrix.json').read_text(encoding='utf-8'))
    (output / matrix['rows'][0]['answer_path']).write_text('invented answer', encoding='utf-8')
    with pytest.raises(ValueError):
        validate_release(output)


def test_saved_matrix_rejects_unrelated_release_mode(tmp_path):
    spec, _, _, _ = fixture(tmp_path)
    output = build(tmp_path, spec).parent
    with pytest.raises(ValueError, match='own bound release audit'):
        validate_release(output, snapshot_audit=True)
