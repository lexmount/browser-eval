"""Compact revisions preserve ordered rules and reject ambiguous edits."""
from copy import deepcopy

import pytest

from lexbench_webfetch.annotation_revisions import _patch_acceptance_contract


def example():
    return {'acceptance_contract': {'schema': 'body_text', 'minimum_records': 0,
            'stable_text_anchors': [{'id': 'a', 'alternatives': ['Alpha']},
                                    {'id': 'b', 'alternatives': ['Beta']}]}}


def test_patch_preserves_unmodified_fields_and_anchor_order():
    case = example()
    patch = {'set': {'minimum_records': 2}, 'anchors': {
        'replace': [{'id': 'b', 'alternatives': ['Beta', 'B']}],
        'append': [{'id': 'c', 'alternatives': ['Gamma']}]}}
    saved = deepcopy(patch)
    _patch_acceptance_contract(case, patch)
    assert case['acceptance_contract'] == {
        'schema': 'body_text', 'minimum_records': 2,
        'stable_text_anchors': [{'id': 'a', 'alternatives': ['Alpha']},
                                {'id': 'b', 'alternatives': ['Beta', 'B']},
                                {'id': 'c', 'alternatives': ['Gamma']}]}
    assert patch == saved


def test_removes_only_named_fields_and_anchors():
    case = example()
    _patch_acceptance_contract(case, {'remove': ['minimum_records'],
                                     'anchors': {'remove': ['a']}})
    assert case['acceptance_contract'] == {
        'schema': 'body_text', 'stable_text_anchors': [{'id': 'b', 'alternatives': ['Beta']}]}


@pytest.mark.parametrize('patch', [
    {'unknown': 1}, {'set': []}, {'remove': ['missing']},
    {'set': {'schema': 'body_text'}, 'remove': ['schema']},
    {'anchors': {'remove': ['missing']}},
    {'anchors': {'replace': [{'id': 'missing'}]}},
    {'anchors': {'append': [{'id': 'a'}]}},
    {'anchors': {'remove': ['a', 'a']}},
    {'anchors': {'remove': ['a'], 'replace': [{'id': 'a'}]}},
    {'anchors': {'replace': [{}]}},
    {'anchors': {'append': {}}},
    {'set': {'stable_text_anchors': []}, 'anchors': {'remove': ['a']}},
])
def test_invalid_edits_fail_without_mutating_case(patch):
    case = example()
    saved = deepcopy(case)
    with pytest.raises(ValueError):
        _patch_acceptance_contract(case, patch)
    assert case == saved


def test_missing_contract_is_rejected():
    with pytest.raises(ValueError):
        _patch_acceptance_contract({}, {'set': {'schema': 'body_text'}})
