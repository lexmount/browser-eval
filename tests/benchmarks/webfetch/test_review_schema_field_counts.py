"""Field requirements belong to the selected schema and its actual records."""
from copy import deepcopy

import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt
from lexbench_webfetch.stable_tasks import validate_acceptance_contract, source_record_hash


def contract(schema, fields=None):
    result = {'version': 1, 'schema': schema,
              'minimum_records': 0 if schema in {'body_text', 'page_identity'} else 1}
    if fields is not None:
        result['field_minimum_records'] = fields
    if schema in {'body_text', 'page_identity'}:
        result['stable_text_anchors'] = [{'id': 'identity', 'alternatives': ['Reference manual']}]
    if schema in {'named_link', 'linked_product'}:
        result.update(hosts=['example.test'], entry_path=r'/item/\d+')
    if schema == 'linked_product':
        result.update(price_patterns=[r'\$\d+'], price_unavailable_patterns=['Unavailable'])
    return result


def score(rules, text):
    case = {'url': 'https://example.test/catalog', 'min_content_chars': 1,
            'acceptance_contract': rules}
    return score_attempt(case, text.encode(), ok=True)


@pytest.mark.parametrize('schema,field', [
    ('named_link', 'family'), ('named_link', 'price'), ('named_link', 'shipping'),
    ('linked_product', 'family'), ('linked_product', 'financing'), ('linked_product', 'price_status'),
    ('product', 'family'), ('product', 'title'), ('catalog_product', 'name'),
    ('review', 'tags'), ('api_operation', 'family'), ('taxonomy_family', 'quote'),
    ('quote', 'magnitude'), ('name_description', 'path'), ('earthquake', 'reviews'),
    ('body_text', 'title'), ('page_identity', 'shipping'),
])
def test_impossible_schema_field_requirement_rejected_before_answer_scoring(schema, field):
    rules = contract(schema, {field: 1})
    with pytest.raises(ValueError):
        validate_acceptance_contract(rules)
    with pytest.raises(ValueError):
        score(rules, 'Reference manual\n[Alpha](/item/1)\n$10')


SCENARIOS = [
    ('product', ['name', 'price', 'shipping', 'financing'],
     '|name|price|shipping|financing|\n|---|---|---|---|\n|Alpha|$10|Free shipping|or 9 x $1.11|'),
    ('catalog_product', ['title', 'price', 'description', 'reviews'],
     '|title|price|description|reviews|\n|---|---|---|---|\n|Alpha|$10|Helpful description|3|'),
    ('quote', ['quote', 'author', 'tags'],
     '|quote|author|tags|\n|---|---|---|\n|A helpful quotation|Ada|insight|'),
    ('review', ['date', 'text'], '|date|text|\n|---|---|\n|2026-10-29|A helpful review|'),
    ('api_operation', ['method', 'path', 'summary'],
     '|method|path|summary|\n|---|---|---|\n|GET|/items|A helpful summary|'),
    ('taxonomy_family', ['family'], '|family|\n|---|\n|Cheloniidae|'),
    ('name_description', ['name', 'description'],
     '|name|description|\n|---|---|\n|Alpha|A helpful description|'),
    ('earthquake', ['magnitude', 'location', 'time', 'depth'],
     '|magnitude|location|time|depth|\n|---|---|---|---|\n|5.1|Pacific ridge|2026-10-29T12:00:00Z|10 km|'),
    ('named_link', ['title', 'url'], '[Alpha](/item/1)'),
    ('linked_product', ['title', 'url', 'price'], '[Alpha](/item/1)\n$10'),
]


@pytest.mark.parametrize('schema,fields,text', SCENARIOS)
def test_supported_schema_and_optional_producer_fields(schema, fields, text):
    rules = contract(schema, dict.fromkeys(fields, 1))
    validate_acceptance_contract(rules)
    result = score(rules, text)
    assert task_passed(True, result), result
    assert all(row['pass'] for row in result['stable_field_results'])


def table_contract(field, kind, *, allow_empty=False):
    return {'version': 1, 'schema': 'table_row', 'minimum_records': 1,
            'field_minimum_records': {field: 1},
            'table_columns': [{'name': 'identifier', 'label': 'Identifier', 'type': 'text'},
                              {'name': field, 'label': 'Value', 'type': kind, 'allow_empty': allow_empty}],
            'record_identity_fields': ['identifier'],
            'table_scope': {'row_selector': '.data', 'cell_selector': ':scope > td',
                            'start_pattern': '^Data$', 'end_pattern': '^End$',
                            'plain_row_pattern': rf'(?P<identifier>\S+) (?P<{field}>\S+)'}}


@pytest.mark.parametrize('field,kind,value', [('units', 'integer', '7'), ('price', 'integer', '7'),
                                            ('display_label', 'text', 'X'), ('family', 'text', 'Oak')])
def test_declared_table_column_uses_its_own_type(field, kind, value):
    rules = table_contract(field, kind)
    validate_acceptance_contract(rules)
    result = score(rules, f'<table><tr class="data"><td>Alpha</td><td>{value}</td></tr></table>')
    assert task_passed(True, result), result
    assert result['stable_field_results'] == [{'field': field, 'count': 1, 'minimum': 1, 'pass': True}]


def test_undeclared_table_field_is_a_bad_contract_not_a_failed_answer():
    rules = table_contract('units', 'integer')
    rules['field_minimum_records'] = {'family': 1}
    with pytest.raises(ValueError):
        score(rules, '<table><tr class="data"><td>Alpha</td><td>7</td></tr></table>')


@pytest.mark.parametrize('text', ['Alpha\n$10', 'Alpha\n$10\nShipping: tomorrow'])
def test_missing_or_invalid_supported_optional_field_is_an_ordinary_failed_answer(text):
    result = score(contract('product', {'shipping': 1}), text)
    assert not task_passed(True, result)
    assert 'missing_required_field:shipping' in result['stable_task_errors']


def test_empty_optional_table_value_does_not_satisfy_required_field_count():
    rules = table_contract('units', 'integer', allow_empty=True)
    result = score(rules, '<table><tr class="data"><td>Alpha</td><td></td></tr></table>')
    assert result['stable_unique_record_count'] == 1
    assert not task_passed(True, result)
    assert result['stable_field_results'] == [{'field': 'units', 'count': 0, 'minimum': 1, 'pass': False}]


@pytest.mark.parametrize('counts', [None, [], {'title': True}, {'title': 0}, {'title': '1'}])
def test_field_count_contract_shape_and_count_types(counts):
    rules = deepcopy(contract('named_link'))
    rules['field_minimum_records'] = counts
    with pytest.raises(ValueError):
        score(rules, '[Alpha](/item/1)')


@pytest.mark.parametrize('schema,fields', [
    ('quote', ['author', 'quote', 'shipping', 'tags']),
    ('api_operation', ['financing', 'method', 'path', 'summary']),
    ('named_link', ['shipping', 'title', 'url']),
])
def test_fixed_source_fields_share_the_schema_requirement_boundary(schema, fields):
    rules = contract(schema)
    rules['source_membership'] = {'kind': 'fixed', 'fields': fields, 'record_hashes': ['0' * 64]}
    with pytest.raises(ValueError, match='Invalid fixed source record bindings'):
        score(rules, 'Reference manual\n[Alpha](/item/1)\n$10')


def test_fixed_source_product_keeps_supported_shipping_field():
    fields = {'name': 'Alpha', 'price': '$10', 'shipping': 'Free shipping'}
    names = sorted(fields)
    rules = contract('product', {'shipping': 1})
    rules['source_membership'] = {'kind': 'fixed', 'fields': names,
                                 'record_hashes': [source_record_hash(fields, names, 'product')]}
    assert task_passed(True, score(rules, 'Alpha\n$10\nFree shipping'))


def scoring_controls():
    cases = []
    for schema, fields, text in SCENARIOS:
        rules = contract(schema, dict.fromkeys(fields, 1))
        cases.append((f'supported-{schema}', rules, text, True))
        if schema in {'named_link', 'linked_product'}:
            cases.append((f'missing-url-{schema}', rules, text.replace('/item/1', '/foreign/1'), False))
    for field, kind, value in [('units', 'integer', '7'), ('price', 'integer', '7'),
                               ('display_label', 'text', 'X'), ('family', 'text', 'Oak')]:
        rules = table_contract(field, kind)
        cases.append((f'table-{field}', rules,
                      f'<table><tr class="data"><td>Alpha</td><td>{value}</td></tr></table>', True))
        cases.append((f'table-missing-{field}', rules,
                      '<table><tr class="data"><td>Alpha</td><td></td></tr></table>', False))
    for suffix, text, passed in [('present', 'Alpha\n$10\nFree shipping', True),
                                  ('missing', 'Alpha\n$10', False),
                                  ('invalid', 'Alpha\n$10\nShipping: tomorrow', False)]:
        cases.append((f'shipping-{suffix}', contract('product', {'shipping': 1}), text, passed))
    cases.append(('empty-optional-column', table_contract('units', 'integer', allow_empty=True),
                  '<table><tr class="data"><td>Alpha</td><td></td></tr></table>', False))
    for identifier, rules, text, passed in cases:
        yield {'id': 'schema-fields-' + identifier, 'category': 'structured-extraction',
               'failure_mode': 'false-negative' if passed else 'false-positive',
               'root_cause': 'field_requirements_use_schema_supported_fields_and_declared_typed_columns',
               'case': {'url': 'https://example.test/catalog', 'min_content_chars': 1,
                        'acceptance_contract': rules},
               'artifact': text, 'ok': True, 'expected_task_pass': passed}
