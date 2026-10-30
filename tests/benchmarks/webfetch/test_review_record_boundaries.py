"""Independent records retain their fields and rendered word boundaries."""
from itertools import permutations

import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt


def scored(case, artifact):
    metrics = score_attempt(case, artifact.encode(), ok=True)
    return task_passed(True, metrics, 'body'), metrics


def composite_case(group):
    return {'url': 'https://example.test/catalog', 'task_kind': 'body', 'min_content_chars': 1,
            'composite_acceptance': {'version': 1, 'groups': [group]}}


def named_group(fields):
    return {'id': 'items', 'kind': 'named_link', 'minimum_records': 1,
            'hosts': ['example.test'], 'entry_path': '/item', 'required_fields': fields}


@pytest.mark.parametrize('card_first', [True, False])
def test_incomplete_table_duplicate_does_not_erase_independent_complete_card(card_first):
    case = composite_case(named_group([{'id': 'price', 'kind': 'price'}]))
    def card(price):
        return '<article><a href="/item">Item</a><p>' + price + '</p></article>'
    table = '<table><tr><td><a href="/item">Item</a></td><td></td></tr></table>'
    def page(price):
        return '<main>' + (card(price) + table if card_first else table + card(price)) + '</main>'
    assert scored(case, page('$12'))[0]
    assert not scored(case, page(''))[0]


FIELD_SETS = [
    [{'id': 'description', 'kind': 'text'}, {'id': 'date', 'kind': 'date_or_time'}],
    [{'id': 'description', 'kind': 'text'}, {'id': 'version', 'kind': 'version'}],
    [{'id': 'description', 'kind': 'text'}, {'id': 'price', 'kind': 'price'}],
]


@pytest.mark.parametrize('fields,value', list(zip(
    FIELD_SETS, ['May 12, 2026', 'v1.2.3', 'USD 12'], strict=True)))
@pytest.mark.parametrize('kind', ['named_link', 'field_report'])
def test_typed_fields_precede_unconstrained_text_in_any_declaration_order(fields, value, kind):
    for ordered in permutations(fields):
        group = named_group(list(ordered))
        if kind == 'field_report':
            group = {'id': 'report', 'kind': 'field_report', 'required_fields': list(ordered)}
        case = composite_case(group)
        artifact = ('[Alpha](/item)\n' if kind == 'named_link' else '') + value
        assert scored(case, artifact + '\nA useful description')[0]
        assert not scored(case, artifact)[0]


@pytest.mark.parametrize('between', ['\n\n', '\n', '\n## More\n'])
def test_adjacent_table_header_is_not_an_additional_record(between):
    case = composite_case({'id': 'items', 'kind': 'table', 'minimum_records': 3,
        'identity_column': 'name', 'required_columns': [
            {'id': 'name', 'kind': 'text', 'aliases': ['Name']},
            {'id': 'description', 'kind': 'text', 'aliases': ['Description']}]})
    def table(name):
        return '|Name|Description|\n|---|---|\n|' + name + '|A useful description|'
    artifact = table('Alpha') + between + table('Beta')
    failed, metrics = scored(case, artifact)
    assert not failed
    assert metrics['composite_group_results'][0]['count'] == 2
    assert scored(case, artifact + between + table('Gamma'))[0]


def stable_case(schema, **extra):
    return {'url': 'https://example.test/catalog', 'task_kind': 'body', 'min_content_chars': 1,
            'acceptance_contract': {'version': 1, 'schema': schema,
                                    'minimum_records': 0 if schema == 'body_text' else 1, **extra}}


@pytest.mark.parametrize('artifact', ['[read](https://example.test/)able',
                                      'pre[fix](https://example.test/)'])
def test_body_anchors_preserve_inline_link_word_adjacency(artifact):
    word = 'prefix' if artifact.startswith('pre') else 'readable'
    case = stable_case('body_text', stable_text_anchors=[{'id': 'word', 'alternatives': [word]}])
    assert scored(case, artifact)[0]
    case['acceptance_contract']['stable_text_anchors'][0]['alternatives'] = [
        'fix' if word == 'prefix' else 'read']
    assert not scored(case, artifact)[0]


@pytest.mark.parametrize('first,second', [('inline', 'inline'), ('inline', 'separate'),
                                           ('separate', 'inline'), ('price_first', 'inline')])
def test_catalog_cards_cannot_borrow_reviews_across_inline_title_price_boundary(first, second):
    case = stable_case('catalog_product')
    def start(name, price, layout):
        return (name + ' ' + price if layout == 'inline' else
                price + '\n' + name if layout == 'price_first' else name + '\n' + price)
    alpha = start('Alpha', '$10', first) + '\nBlue machine'
    beta = start('Beta', '$20', second) + '\n3 reviews'
    assert not scored(case, alpha + '\n' + beta)[0]
    assert scored(case, alpha + '\n2 reviews\n' + beta)[0]
    assert scored(case, alpha + '\n' + start('Beta', '$20', second)
                  + '\nRed machine\n3 reviews')[0]


@pytest.mark.parametrize('typed,label,value', [
    ('date_or_time', 'Published', 'May 12, 2026'),
    ('price', 'Cost', '$12'), ('integer', 'Reviews', '3 reviews')])
@pytest.mark.parametrize('kind', ['named_link', 'field_report'])
def test_consumed_declared_field_label_cannot_become_description(typed, label, value, kind):
    fields = [{'id': 'value', 'kind': typed, 'aliases': [label]},
              {'id': 'description', 'kind': 'text'}]
    group = named_group(fields) if kind == 'named_link' else {
        'id': 'report', 'kind': kind, 'required_fields': fields}
    case = composite_case(group)
    artifact = ('[Alpha](/item)\n' if kind == 'named_link' else '') + label + ': ' + value
    assert not scored(case, artifact)[0]
    assert scored(case, artifact + '\nA useful description')[0]


def test_date_integer_and_description_have_stable_assignment_in_all_orders():
    fields = [{'id': 'date', 'kind': 'date_or_time'}, {'id': 'count', 'kind': 'integer'},
              {'id': 'description', 'kind': 'text'}]
    for ordered in permutations(fields):
        case = composite_case(named_group(list(ordered)))
        artifact = '[Alpha](/item)\nMay 12, 2026\n3'
        assert not scored(case, artifact)[0]
        assert scored(case, artifact + '\nA useful description')[0]


def test_patterned_fields_keep_original_lookahead_in_every_order():
    fields = [{'id': 'venue', 'kind': 'text', 'pattern': r'^Hall A(?=\s+Town, TX$)'},
              {'id': 'city', 'kind': 'text', 'pattern': r'Town, TX$'}]
    for ordered in permutations(fields):
        case = composite_case(named_group(list(ordered)))
        assert scored(case, '[Alpha](/item)\nHall A Town, TX')[0]
        assert not scored(case, '[Alpha](/item)\nHall A')[0]


def test_overlapping_pattern_candidates_find_a_disjoint_assignment():
    fields = [{'id': 'flexible', 'kind': 'text', 'pattern': 'Alpha|Beta'},
              {'id': 'specific', 'kind': 'text', 'pattern': 'Alpha'}]
    for ordered in permutations(fields):
        case = composite_case(named_group(list(ordered)))
        assert scored(case, '[Item](/item)\nAlpha Beta')[0]
        assert not scored(case, '[Item](/item)\nAlpha')[0]


def test_wide_incomplete_card_does_not_permute_partial_diagnostics():
    fields = [{'id': 'description_' + str(index), 'kind': 'text'} for index in range(12)]
    case = composite_case(named_group(fields))
    artifact = '[Item](/item)\n' + '\n'.join('Independent paragraph ' + str(i) for i in range(11))
    assert not scored(case, artifact)[0]
    assert scored(case, artifact + '\nThe final independent paragraph')[0]


def test_missing_constrained_field_does_not_search_free_text_permutations():
    fields = [{'id': 'description_' + str(index), 'kind': 'text'} for index in range(10)]
    fields.append({'id': 'summary', 'kind': 'text', 'pattern': 'Required summary'})
    case = composite_case(named_group(fields))
    artifact = '[Item](/item)\n' + '\n'.join('Independent paragraph ' + str(i) for i in range(10))
    assert not scored(case, artifact)[0]
    assert scored(case, artifact + '\nRequired summary')[0]


def test_repeated_tabular_headers_are_boundaries_without_markdown_dividers():
    case = composite_case({'id': 'items', 'kind': 'table', 'minimum_records': 3,
        'identity_column': 'name', 'required_columns': [
            {'id': 'name', 'kind': 'text', 'aliases': ['Name']},
            {'id': 'description', 'kind': 'text', 'aliases': ['Description']}]})
    artifact = 'Name\tDescription\nAlpha\tFirst item\nName\tDescription\nBeta\tSecond item'
    failed, metrics = scored(case, artifact)
    assert not failed
    assert metrics['composite_group_results'][0]['count'] == 2
    assert scored(case, artifact + '\nName\tDescription\nGamma\tThird item')[0]


def test_catalog_description_with_price_is_not_a_new_card():
    case = stable_case('catalog_product')
    artifact = 'Alpha $10\nDescription: Includes accessories worth $12\n3 reviews'
    assert scored(case, artifact)[0]
    missing = artifact.replace('Description: Includes accessories worth $12\n', '')
    assert not scored(case, missing)[0]


def test_orphan_price_cannot_supply_the_price_of_a_following_inline_card():
    case = stable_case('catalog_product')
    artifact = '$10\nBeta $20\nRed machine\n3 reviews'
    passed, metrics = scored(case, artifact)
    assert passed
    valid = [row for row in metrics['stable_records'] if row['valid']]
    assert [row['fields']['price'] for row in valid] == ['$20']
    assert not scored(case, artifact.replace('Red machine\n', ''))[0]
