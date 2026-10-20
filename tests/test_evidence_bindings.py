"""Bound evidence rejects malformed identities before filesystem or view lookup."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import hashlib

import pytest

from harness.web_fetch import publication_privacy as privacy


@pytest.mark.parametrize('value,field', [
    (None, 'evidence_binding'), ([], 'evidence_binding'),
    ({'sha256': 'a' * 64}, 'evidence_binding.path'),
    ({'path': ''}, 'evidence_binding.path'),
    ({'path': '  '}, 'evidence_binding.path'),
    ({'path': 12}, 'evidence_binding.path'),
    ({'path': 'answer.txt'}, 'evidence_binding.sha256'),
    ({'path': 'answer.txt', 'sha256': None}, 'evidence_binding.sha256'),
    ({'path': 'answer.txt', 'sha256': 'A' * 64}, 'evidence_binding.sha256'),
    ({'path': 'answer.txt', 'sha256': 'z' * 64}, 'evidence_binding.sha256'),
    ({'path': 'answer.txt', 'sha256': 'a' * 63}, 'evidence_binding.sha256'),
])
def test_invalid_binding_has_a_field_error_before_view_lookup(tmp_path, monkeypatch, value, field):
    def unexpected_lookup(*args, **kwargs):
        pytest.fail('Malformed identity reached public evidence lookup')
    monkeypatch.setattr(privacy, 'bound_public_view', unexpected_lookup)
    with pytest.raises(ValueError, match=field):
        privacy.read_evidence_binding(tmp_path, value)


def test_binding_parser_preserves_saved_mapping_and_checks_actual_bytes(tmp_path):
    path = tmp_path / 'answer.txt'
    path.write_bytes(b'original answer')
    binding = {'path': 'answer.txt', 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
               'existing_receipt_field': {'keep': True}}
    before = deepcopy(binding)
    identity = privacy.EvidenceBinding.from_mapping(binding, field='receipt.answer')
    assert identity.path == binding['path'] and identity.sha256 == binding['sha256']
    assert privacy.read_evidence_binding(tmp_path, binding, allow_public_view=False) == path
    assert binding == before
    with pytest.raises(FrozenInstanceError):
        identity.path = 'other.txt'
    path.write_bytes(b'changed answer')
    with pytest.raises(ValueError, match='evidence binding changed'):
        privacy.read_evidence_binding(tmp_path, binding, allow_public_view=False)


def test_binding_parser_reports_caller_field():
    with pytest.raises(ValueError, match=r'receipt.answer.sha256'):
        privacy.EvidenceBinding.from_mapping({'path': 'answer.txt'}, field='receipt.answer')


def test_valid_binding_shape_does_not_bypass_path_safety(tmp_path):
    with pytest.raises(ValueError):
        privacy.read_evidence_binding(tmp_path, {'path': '../outside.txt', 'sha256': 'a' * 64})
