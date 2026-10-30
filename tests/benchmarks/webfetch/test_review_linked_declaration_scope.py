"""Linked-record declarations cannot silently constrain unrelated schemas."""
from copy import deepcopy
import hashlib

import pytest

from lexbench_webfetch.dynamic_lists import named_entry_source_pair_hash
from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt
from lexbench_webfetch.stable_tasks import validate_acceptance_contract


DECLARATIONS = {
    'hosts': ['example.test'], 'entry_path': r'/item/\d+',
    'entry_scope': {'html_selector': '.required'},
    'entry_identity_pattern': r'(/item/\d+)', 'entry_context': 'list_or_card',
    'name_validation': 'nonempty', 'identity_query_keys': ['id'],
    'identity_query_paths': [r'/item/\d+'],
    'record_name_exclusions': ['Other title'], 'title_exclusion_patterns': ['^Other'],
    'link_base_url': 'https://example.test/catalog/',
    'source_record_pair_hashes': ['0' * 64],
    'source_record_pairs_sha256': hashlib.sha256(('0' * 64).encode()).hexdigest(),
}
NON_LINKED = ['product', 'catalog_product', 'quote', 'review', 'api_operation',
              'name_description', 'earthquake', 'taxonomy_family', 'table_row',
              'body_text', 'page_identity']


def contract(schema):
    result = {'version': 1, 'schema': schema,
              'minimum_records': 0 if schema in {'body_text', 'page_identity'} else 1}
    if schema in {'body_text', 'page_identity'}:
        result['stable_text_anchors'] = [{'id': 'identity', 'alternatives': ['Reference manual']}]
    if schema == 'table_row':
        result.update(table_columns=[{'name': 'identifier', 'label': 'Identifier', 'type': 'text'},
                                     {'name': 'value', 'label': 'Value', 'type': 'integer'}],
                      record_identity_fields=['identifier'], table_scope={
                          'row_selector': 'tr', 'cell_selector': ':scope > td',
                          'start_pattern': '^Data$', 'end_pattern': '^End$',
                          'plain_row_pattern': r'(?P<identifier>\S+) (?P<value>\d+)'})
    if schema in {'named_link', 'linked_product'}:
        result.update(hosts=['example.test'], entry_path=r'/item/\d+')
    if schema == 'linked_product':
        result.update(price_patterns=[r'\$\d+'], price_exclusion_patterns=[])
    return result


def score(rules, text):
    return score_attempt({'url': 'https://example.test/catalog', 'min_content_chars': 1,
                          'acceptance_contract': rules}, text.encode(), ok=True)


@pytest.mark.parametrize('schema', NON_LINKED)
@pytest.mark.parametrize('field,value', DECLARATIONS.items())
def test_linked_declarations_rejected_for_non_linked_schema(schema, field, value):
    rules = contract(schema)
    validate_acceptance_contract(rules)
    rules[field] = deepcopy(value)
    with pytest.raises(ValueError):
        validate_acceptance_contract(rules)
    with pytest.raises(ValueError):
        score(rules, 'Reference manual\nAlpha\n$10')


@pytest.mark.parametrize('schema', ['named_link', 'linked_product'])
@pytest.mark.parametrize('field,value', DECLARATIONS.items())
def test_supported_linked_declarations_preserve_public_scoring(schema, field, value):
    rules = contract(schema)
    rules[field] = deepcopy(value)
    if field == 'identity_query_paths':
        rules['identity_query_keys'] = ['id']
    if field in {'source_record_pair_hashes', 'source_record_pairs_sha256'}:
        digest = named_entry_source_pair_hash('Alpha', 'https://example.test/item/1?id=x', rules)
        rules.update(source_record_pair_hashes=[digest],
                     source_record_pairs_sha256=hashlib.sha256(digest.encode()).hexdigest())
    text = '<section class="required"><a href="/item/1?id=x">Alpha</a><p>$10</p></section>'
    validate_acceptance_contract(rules)
    assert task_passed(True, score(rules, text))


@pytest.mark.parametrize('field', ['entry_scope', 'source_record_pair_hashes',
                                  'source_record_pairs_sha256', 'entry_identity_pattern'])
def test_even_empty_linked_declaration_is_not_a_non_linked_requirement(field):
    rules = contract('product')
    rules[field] = None
    with pytest.raises(ValueError):
        score(rules, 'Alpha\n$10')
