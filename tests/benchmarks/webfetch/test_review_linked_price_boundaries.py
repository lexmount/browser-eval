"""Linked price requirements follow declared extraction and existing absence hashes."""
from copy import deepcopy
import hashlib
import json

import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt
from lexbench_webfetch.stable_tasks import source_record_hash


def contract(pattern=r'₹\d+', *, require_price=True, bound=None):
    rules = {'version': 1, 'schema': 'linked_product', 'minimum_records': 1,
             'hosts': ['example.test'], 'entry_path': r'/item/\d+',
             'price_patterns': [pattern], 'price_exclusion_patterns': [],
             'price_unavailable_patterns': ['Unavailable']}
    if require_price:
        rules['field_minimum_records'] = {'price': 1}
    if bound is not None:
        fields = {'title': 'Alpha', 'url': 'https://example.test/item/1'}
        if bound != 'unavailable':
            fields['price'] = bound
        names = ['price', 'title', 'url']
        rules['source_membership'] = {'kind': 'fixed', 'fields': names,
                                     'record_hashes': [source_record_hash(fields, names, 'linked_product')]}
    return rules


def score(rules, text):
    return score_attempt({'url': 'https://example.test/catalog', 'min_content_chars': 1,
                          'acceptance_contract': rules}, text.encode(), ok=True)


CASES = [
    ('rupee', contract(), '[Alpha](/item/1)\n₹100', True),
    ('dollar', contract(r'\$\d+'), '[Alpha](/item/1)\n$100', True),
    ('yen', contract(r'円\d+'), '[Alpha](/item/1)\n円100', True),
    ('wrong-currency', contract(), '[Alpha](/item/1)\n$100', False),
    ('missing-price', contract(), '[Alpha](/item/1)', False),
    ('unavailable-required', contract(), '[Alpha](/item/1)\nUnavailable', False),
    ('unavailable-allowed', contract(require_price=False), '[Alpha](/item/1)\nUnavailable', True),
    ('bound-available-match', contract(bound='₹100'), '[Alpha](/item/1)\n₹100', True),
    ('bound-available-wrong', contract(bound='₹100'), '[Alpha](/item/1)\n₹101', False),
    ('bound-available-unavailable', contract(require_price=False, bound='₹100'),
     '[Alpha](/item/1)\nUnavailable', False),
    ('bound-unavailable-match', contract(require_price=False, bound='unavailable'),
     '[Alpha](/item/1)\nUnavailable', True),
    ('bound-unavailable-wrong', contract(require_price=False, bound='unavailable'),
     '[Alpha](/item/1)\n₹100', False),
    ('bound-unavailable-required', contract(bound='unavailable'),
     '[Alpha](/item/1)\nUnavailable', False),
]


@pytest.mark.parametrize('identifier,rules,text,passed', CASES)
def test_declared_linked_price_and_source_membership(identifier, rules, text, passed):
    result = score(rules, text)
    assert task_passed(True, result) is passed, result
    if 'unavailable' in identifier and text.endswith('Unavailable'):
        assert result['stable_record_count'] == 1
    if identifier == 'bound-available-unavailable':
        assert 'source_record_mismatch' in result['stable_task_errors']
    if identifier == 'bound-unavailable-match':
        assert result['stable_complete_record_count'] == 1
        assert 'source_record_mismatch' not in result['stable_task_errors']
    if identifier in {'unavailable-required', 'bound-unavailable-required'}:
        assert result['stable_field_results'] == [{'field': 'price', 'count': 0, 'minimum': 1, 'pass': False}]


@pytest.mark.parametrize('schema', ['named_link', 'linked_product'])
def test_canonical_link_fields_reuse_completed_name_validation(schema):
    rules = contract(r'\$\d+')
    rules.update(schema=schema, name_validation='nonempty', field_minimum_records={'title': 1, 'url': 1})
    if schema == 'named_link':
        for key in ['price_patterns', 'price_exclusion_patterns', 'price_unavailable_patterns']:
            del rules[key]
    assert task_passed(True, score(rules, '[A](/item/1)\n$10'))
    assert not task_passed(True, score(rules, '[A](/foreign/1)\n$10'))


def test_none_selected_value_uses_existing_omitted_field_absence_hash():
    fields = {'title': 'Alpha', 'url': 'https://example.test/item/1'}
    names = ['price', 'title', 'url']
    expected = hashlib.sha256(json.dumps(['', 'alpha', fields['url']], ensure_ascii=False,
                                         separators=(',', ':')).encode()).hexdigest()
    assert source_record_hash(fields, names, 'linked_product') == expected
    assert source_record_hash({**fields, 'price': None}, names, 'linked_product') == expected


def test_omitted_optional_product_field_preserves_existing_binding():
    fields = {'name': 'Alpha', 'price': '$10'}
    names = ['name', 'price', 'shipping']
    expected = hashlib.sha256(b'["alpha","$10",""]').hexdigest()
    assert source_record_hash(fields, names, 'product') == expected
    rules = {'version': 1, 'schema': 'product', 'minimum_records': 1,
             'source_membership': {'kind': 'fixed', 'fields': names, 'record_hashes': [expected]}}
    assert task_passed(True, score(rules, 'Alpha\n$10'))


def scoring_controls():
    cases = list(CASES)
    for schema in ['named_link', 'linked_product']:
        rules = contract(r'\$\d+')
        rules.update(schema=schema, name_validation='nonempty', field_minimum_records={'title': 1, 'url': 1})
        if schema == 'named_link':
            for key in ['price_patterns', 'price_exclusion_patterns', 'price_unavailable_patterns']:
                del rules[key]
        for path, passed in [('/item/1', True), ('/foreign/1', False)]:
            cases.append((f'completed-name-{schema}-{passed}', rules, f'[A]({path})\n$10', passed))
    for identifier, rules, text, passed in cases:
        yield {'id': 'linked-price-' + identifier, 'category': 'structured-extraction',
               'failure_mode': 'false-negative' if passed else 'false-positive',
               'root_cause': 'linked_price_requirements_follow_declared_extraction_and_existing_absence_hash',
               'case': {'url': 'https://example.test/catalog', 'min_content_chars': 1,
                        'acceptance_contract': deepcopy(rules)},
               'artifact': text, 'ok': True, 'expected_task_pass': passed}
