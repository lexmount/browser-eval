"""Linked records share canonical identities and exclude literal examples."""
import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt


def scored(case, artifact):
    metrics = score_attempt(case, artifact.encode(), ok=True)
    return task_passed(True, metrics, 'body'), metrics


def composite_case(hosts, fields, **extra):
    group = {'id': 'items', 'kind': 'named_link', 'minimum_records': 1,
             'hosts': hosts, 'entry_path': '/item', **extra}
    if fields:
        group['required_fields'] = [{'id': 'price', 'kind': 'price'}]
    return {'url': 'https://example.test/catalog', 'task_kind': 'body', 'min_content_chars': 1,
            'composite_acceptance': {'version': 1, 'groups': [group]}}


@pytest.mark.parametrize('hosts', [['*'], ['example.test']])
@pytest.mark.parametrize('fields', [False, True])
def test_declared_query_subset_has_same_identity_with_or_without_fields(hosts, fields):
    case = composite_case(hosts, fields, identity_query_keys=['id', 'v'])
    assert scored(case, '[Widget](/item?id=1)\n$10')[0]
    assert not scored(case, '[Widget](/item?other=1)\n$10')[0]


@pytest.mark.parametrize('hosts', [['*'], ['example.test']])
@pytest.mark.parametrize('fields', [False, True])
def test_declared_path_identity_takes_precedence_over_query_identity(hosts, fields):
    case = composite_case(hosts, fields, minimum_records=2, entry_path=r'/item/\d+',
                          entry_identity_pattern=r'/item/(\d+)', identity_query_keys=['id'])
    first = '[Widget Alpha](/item/1)\n$10'
    second = '[Widget Beta](/item/2)\n$20'
    assert scored(case, first + '\n\n' + second)[0]
    duplicate = '[Widget Alpha](/item/1?id=A)\n$10\n\n[Widget Beta](/item/1?id=B)\n$20'
    assert not scored(case, duplicate)[0]


@pytest.mark.parametrize('hosts', [['*'], ['example.test']])
@pytest.mark.parametrize('fields', [False, True])
@pytest.mark.parametrize('layout', ['backticks', 'tildes', 'inline'])
def test_literal_link_syntax_cannot_introduce_a_named_record(hosts, fields, layout):
    case = composite_case(hosts, fields, entry_path=r'/item/\d+')
    artifact = '[Widget](/item/1)\n$10'
    literal = ('`[Widget](/item/1)`\n$10' if layout == 'inline' else
               ('```' if layout == 'backticks' else '~~~') + '\n' + artifact + '\n'
               + ('```' if layout == 'backticks' else '~~~'))
    assert scored(case, artifact)[0]
    assert not scored(case, literal)[0]
    assert scored(case, literal + '\n\n[Real widget](/item/2)\n$20')[0]


def stable_case(schema='named_link', **extra):
    if schema == 'linked_product':
        extra = {'price_patterns': [r'\$\d+'],
                 'price_exclusion_patterns': [r'out of stock'], **extra}
    return {'url': 'https://example.test/catalog', 'task_kind': 'body', 'min_content_chars': 1,
            'acceptance_contract': {'version': 1, 'schema': schema, 'minimum_records': 2,
                'hosts': ['example.test'], 'entry_path': r'/items/[^/]+', **extra}}


@pytest.mark.parametrize('schema', ['named_link', 'linked_product'])
@pytest.mark.parametrize('urls,extra', [
    (('/items/Alpha', '/items/alpha'), {}),
    (('/items/item?id=Alpha', '/items/item?id=alpha'), {'identity_query_keys': ['id']}),
])
def test_stable_linked_record_identity_preserves_path_and_query_case(schema, urls, extra):
    case = stable_case(schema, field_minimum_records={'title': 2}, **extra)
    def text(first, second):
        return '[Widget Alpha](' + first + ')\n$10\n\n[Widget Beta](' + second + ')\n$20'
    assert scored(case, text(*urls))[0]
    assert not scored(case, text(urls[0], urls[0]))[0]


@pytest.mark.parametrize('hosts', [['*'], ['example.test']])
def test_invalid_present_query_values_reject_only_their_candidates(hosts):
    case = composite_case(hosts, True, identity_query_keys=['id', 'v'])
    for query in ['id=', 'id=1&id=2', 'id=1&v=', 'other=1']:
        artifact = '[Invalid widget](/item?' + query + ')\n$10'
        assert not scored(case, artifact)[0]
        assert scored(case, artifact + '\n\n[Real widget](/item?id=2)\n$20')[0]


@pytest.mark.parametrize('hosts', [['*'], ['example.test']])
def test_named_composite_field_ownership_preserves_query_and_path_case(hosts):
    for path, extra, first, second in [
        (r'/items/[^/]+', {}, '/items/Alpha', '/items/alpha'),
        ('/item', {'identity_query_keys': ['id']}, '/item?id=Alpha', '/item?id=alpha'),
    ]:
        case = composite_case(hosts, True, minimum_records=2, entry_path=path, **extra)
        artifact = '[Widget Alpha](' + first + ')\n$10\n\n[Widget Beta](' + second + ')\n$20'
        assert scored(case, artifact)[0]
        assert not scored(case, artifact.replace(second, first))[0]


@pytest.mark.parametrize('hosts', [['*'], ['example.test']])
def test_literal_mask_precedes_native_projection_and_field_ownership(hosts):
    case = composite_case(hosts, True, entry_path=r'/item/\d+')
    literal = '```md\n[Widget](/item/1)\n$10\n```'
    assert not scored(case, '<main>\n' + literal + '\n</main>')[0]
    assert not scored(case, '[Real widget](/item/2)\n\n' + literal)[0]
    assert scored(case, '<main>\n' + literal
                  + '\n<article><a href="/item/2">Real widget</a><p>$20</p></article></main>')[0]
    assert scored(case, '[Real widget](/item/2)\n`$20`')[0]


def test_fenced_date_example_cannot_satisfy_a_declared_date_field():
    case = composite_case(['example.test'], False, entry_path=r'/item/\d+')
    case['composite_acceptance']['groups'][0]['required_fields'] = [
        {'id': 'date', 'kind': 'date_or_time'}]
    artifact = '[Widget](/item/1)\nDate: 2026-09-28'
    assert scored(case, artifact)[0]
    assert not scored(case, '```\n' + artifact + '\n```')[0]
