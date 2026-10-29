"""JSON declaration types are checked before collection and compiler operations."""
import hashlib

import pytest

from lexbench_webfetch.stable_tasks import validate_acceptance_contract


def linked_contract(**changes):
    hashes = ['1' * 64, '2' * 64]
    return {'version': 1, 'schema': 'named_link', 'minimum_records': 1,
            'hosts': ['example.test'], 'entry_path': r'/item/\d+',
            'source_record_pair_hashes': hashes,
            'source_record_pairs_sha256': hashlib.sha256('\n'.join(hashes).encode()).hexdigest(),
            **changes}


@pytest.mark.parametrize('hashes', [[{}], [[]], [None], [False], [12],
                                    ['1' * 64, 12], ['1' * 64, {}],
                                    ['invalid'], ['1' * 64, '1' * 64]])
def test_invalid_hash_members_are_rejected_before_deduplication(hashes):
    with pytest.raises(ValueError):
        validate_acceptance_contract(linked_contract(source_record_pair_hashes=hashes))


def test_valid_sorted_source_pair_hashes_remain_supported():
    validate_acceptance_contract(linked_contract())


@pytest.mark.parametrize('value', [None, False, 12, [], {}])
@pytest.mark.parametrize('field', ['membership_kind', 'count_list_type', 'name_validation'])
def test_related_enum_fields_reject_nonstring_json_values(field, value):
    rules = linked_contract()
    if field == 'membership_kind':
        rules['source_membership'] = {'kind': value}
    elif field == 'count_list_type':
        rules['record_count_policy'] = {
            'list_type': value, 'source_observed_count': 1, 'required_ratio': 1.0,
            'source_observation_counts': [1, 1], 'source_evidence_sha256': 'a' * 64,
            'reason': 'Repeated independent source counts'}
    else:
        rules[field] = value
    with pytest.raises(ValueError):
        validate_acceptance_contract(rules)


@pytest.mark.parametrize('schema', ['body_text', 'page_identity'])
@pytest.mark.parametrize('field,value', [('minimum_records', False), ('minimum_records', 0.0),
                                        ('maximum_records', False), ('maximum_records', 0.0),
                                        ('maximum_records', []), ('maximum_records', {})])
def test_recordless_schemas_require_integer_zero_counts(schema, field, value):
    rules = {'version': 1, 'schema': schema, 'minimum_records': 0,
             'stable_text_anchors': [{'id': 'page', 'alternatives': ['Catalog']}]}
    rules[field] = value
    with pytest.raises(ValueError):
        validate_acceptance_contract(rules)


@pytest.mark.parametrize('schema', ['body_text', 'page_identity'])
@pytest.mark.parametrize('maximum', [None, 0])
def test_recordless_integer_zero_declarations_remain_valid(schema, maximum):
    validate_acceptance_contract({'version': 1, 'schema': schema, 'minimum_records': 0,
        'maximum_records': maximum, 'stable_text_anchors': [{'id': 'page', 'alternatives': ['Catalog']}]})


@pytest.mark.parametrize('kind', ['regex', 'selector'])
@pytest.mark.parametrize('location', ['exclusion', 'entry_scope'])
def test_invalid_declaration_patterns_raise_contract_validation_errors(kind, location):
    rules = linked_contract()
    if location == 'exclusion':
        rules['text_exclusions'] = {'text_patterns' if kind == 'regex' else 'html_selectors': ['[']}
    else:
        rules['entry_scope'] = {'html_selector': '[' if kind == 'selector' else 'main'}
        if kind == 'regex':
            rules['entry_scope']['start_pattern'] = '['
    with pytest.raises(ValueError):
        validate_acceptance_contract(rules)
