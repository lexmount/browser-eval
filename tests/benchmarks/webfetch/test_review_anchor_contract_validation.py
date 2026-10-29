"""Malformed declarations fail at validation before scope rules inspect them."""
import copy

import pytest

from lexbench_webfetch.stable_tasks import validate_acceptance_contract


def contract(**changes):
    return {'version': 1, 'schema': 'product', 'minimum_records': 1,
            'content_scope': 'target_content',
            'stable_text_anchors': [{'id': 'item', 'alternatives': ['Widget'],
                                     'source_location': 'main > article'}], **changes}


@pytest.mark.parametrize('scope', ['legacy', 'target_content', 'full_page_stable_coverage'])
@pytest.mark.parametrize('anchors', [None, False, 12, 'anchor', {}, (None,),
                                    [None], [False], [[]], [1], ['anchor']])
def test_invalid_anchor_collections_and_members_raise_validation_errors(scope, anchors):
    with pytest.raises(ValueError):
        validate_acceptance_contract(contract(content_scope=scope, stable_text_anchors=anchors))


@pytest.mark.parametrize('scope', ['legacy', 'target_content', 'full_page_stable_coverage'])
@pytest.mark.parametrize('location', [None, False, 12, 1.5, [], {}, '', '  '])
def test_invalid_source_locations_raise_validation_errors(scope, location):
    rules = contract(content_scope=scope)
    rules['stable_text_anchors'][0]['source_location'] = location
    with pytest.raises(ValueError):
        validate_acceptance_contract(rules)


@pytest.mark.parametrize('field', ['content_scope', 'stable_text_role'])
@pytest.mark.parametrize('value', [None, False, 12, [], {}])
def test_scope_and_role_declarations_are_strings(field, value):
    with pytest.raises(ValueError):
        validate_acceptance_contract(contract(**{field: value}))


@pytest.mark.parametrize('location', ['nav', 'main > footer.notice', 'NAV > a'])
def test_valid_navigation_anchors_are_rejected_only_in_target_scope(location):
    rules = contract()
    rules['stable_text_anchors'][0]['source_location'] = location
    with pytest.raises(ValueError, match='cannot supply'):
        validate_acceptance_contract(rules)
    for scope in ['legacy', 'full_page_stable_coverage']:
        validate_acceptance_contract({**copy.deepcopy(rules), 'content_scope': scope})


def test_valid_target_anchors_and_optional_locations_remain_supported():
    rules = contract()
    validate_acceptance_contract(rules)
    del rules['stable_text_anchors'][0]['source_location']
    validate_acceptance_contract(rules)
