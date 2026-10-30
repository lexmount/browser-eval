"""Malformed persisted provenance is rejected without aborting validation."""
import copy
import hashlib
import json

import pytest

from harness.web_fetch.reference_lifecycle import valid_scoring_revision
from lexbench_webfetch.scoring_regressions import build_correction_provenance, valid_correction_provenance


def receipt():
    return {'schema_version': 2, 'task_decision_changes': [],
            'correction_provenance': build_correction_provenance(
                'answer-surface', 'Visible answer text must be preserved independently of metadata.',
                ['answer-title-supplies-required-fact'])}


@pytest.mark.parametrize('field,value', [('schema_version', []), ('schema_version', {}),
    ('schema_version', True), ('schema_version', 2.0), ('task_decision_changes', [None]),
    ('task_decision_changes', ['not an object']), ('task_decision_changes', [True]),
    ('task_decision_changes', [{}]), ('task_decision_changes', None)])
def test_invalid_receipt_returns_false_in_direct_and_history_validation(tmp_path, field, value):
    row = receipt()
    assert valid_correction_provenance(row)
    row[field] = value
    assert valid_correction_provenance(row) is False
    raw = b'{"cases": []}'
    (tmp_path / 'dataset.json').write_bytes(raw)
    (tmp_path / 'results.jsonl').write_bytes(b'')
    row.update(kind='audited_scoring_rule_revision', provider_calls=0, changed_cases=[],
               input_dataset_sha256='a' * 64, input_results_sha256='b' * 64,
               output_dataset_sha256=hashlib.sha256(raw).hexdigest(),
               output_results_sha256=hashlib.sha256(b'').hexdigest())
    (tmp_path / 'scoring-revisions.json').write_text(json.dumps([row]))
    assert valid_scoring_revision(tmp_path) is False


@pytest.mark.parametrize('value', [None, [], 'not an object', {'schema_version': 2,
                                                           'correction_provenance': []}])
def test_invalid_outer_receipt_is_rejected(value):
    assert valid_correction_provenance(value) is False


def test_invalid_category_is_rejected_without_a_hashability_error():
    row = copy.deepcopy(receipt())
    row['correction_provenance']['category'] = {}
    assert valid_correction_provenance(row) is False


@pytest.mark.parametrize('number', [False, 0.0])
def test_verdict_counts_require_integer_counts_not_equal_boolean_or_float_values(number):
    row = receipt()
    row['correction_provenance']['verdict_corrections'] = {
        'false_positives': number, 'false_negatives': number, 'total': number}
    assert valid_correction_provenance(row) is False
