from unittest.mock import patch

import pytest

from harness.web_fetch.safety import copy_evidence_file


def test_frozen_copy_has_independent_identity_and_writes(tmp_path):
    source, target = tmp_path/'source.txt', tmp_path/'target.txt'
    source.write_text('saved measurement')
    copy_evidence_file(source, target)
    assert target.read_bytes() == source.read_bytes()
    assert target.stat().st_ino != source.stat().st_ino
    target.write_text('generated presentation')
    assert source.read_text() == 'saved measurement'


def test_unsupported_clone_falls_back_to_an_independent_copy(tmp_path):
    source, target = tmp_path/'source.txt', tmp_path/'target.txt'
    source.write_bytes(b'saved bytes')
    with patch('harness.web_fetch.safety.sys.platform', 'darwin'), \
         patch('ctypes.CDLL', side_effect=OSError('clone unavailable')):
        copy_evidence_file(source, target)
    assert target.read_bytes() == b'saved bytes'
    assert target.stat().st_ino != source.stat().st_ino


@pytest.mark.parametrize('side', ['source', 'destination'])
def test_frozen_copy_rejects_symlinked_inputs_or_outputs(tmp_path, side):
    real = tmp_path/'real.txt'
    real.write_bytes(b'private original')
    link = tmp_path/'link.txt'
    link.symlink_to(real)
    source = link if side == 'source' else real
    target = link if side == 'destination' else tmp_path/'copy.txt'
    with pytest.raises(ValueError, match='symlink'):
        copy_evidence_file(source, target)
    assert real.read_bytes() == b'private original'
