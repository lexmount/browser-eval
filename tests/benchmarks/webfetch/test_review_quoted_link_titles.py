"""Quoted link metadata cannot change destination pairing or visible evidence."""
import pytest

from lexbench_webfetch.evaluation import evaluate_response
from lexbench_webfetch.reference_quality import markdown_links, visible_markdown_links
from lexbench_webfetch.stable_tasks import validate_acceptance_contract


@pytest.mark.parametrize('title', ['"special evidence ("', '"special evidence )"',
                                  "'special evidence ('", r'"special evidence \" ("',
                                  '"special evidence ["'])
@pytest.mark.parametrize('bracketed', [False, True])
def test_title_delimiters_are_metadata_not_visible_answer_text(title, bracketed):
    destination = 'https://example.test/item/1'
    raw_destination = '<' + destination + '>' if bracketed else destination
    answer = '[Open](' + raw_destination + ' ' + title + ')'
    links = markdown_links(answer)
    assert len(links) == 1
    assert links[0].destination == destination
    assert links[0].title.startswith('special evidence')
    assert visible_markdown_links(answer) == 'Open'
    for scope, pattern, passed in [('visible_text', 'special evidence', False),
                                   ('visible_text', 'Open', True), ('output', 'special evidence', True)]:
        case = {'url': 'https://example.test/', 'required_patterns': [
            {'id': 'pattern', 'match_scope': scope, 'pattern': pattern}]}
        assert evaluate_response(case, answer.encode())['passed'] is passed


def test_nested_destination_parentheses_and_escaped_title_quotes_are_preserved():
    link = markdown_links(r'[Open](https://example.test/item/(1) "say \"hello\" (")')[0]
    assert link.destination == 'https://example.test/item/(1)'
    assert link.title == 'say "hello" ('


def test_unclosed_title_does_not_consume_a_later_valid_link():
    links = markdown_links('[Broken](https://example.test/ "unfinished\n\n[Open](/item/1)')
    assert any(link.label == 'Open' and link.destination == '/item/1' for link in links)


def contract(schema, identity_pattern):
    value = {'version': 1, 'schema': schema, 'minimum_records': 1,
             'hosts': ['example.test'], 'entry_path': r'/item/\d+',
             'stable_text_anchors': [], 'entry_identity_pattern': identity_pattern}
    if schema == 'linked_product':
        value['price_patterns'] = [r'\$\d+']
    return value


@pytest.mark.parametrize('schema', ['named_link', 'linked_product'])
@pytest.mark.parametrize('pattern', ['[', '', 123, r'/item/\d+', r'/item/(\d+)(x)?'])
def test_all_linked_contracts_reject_invalid_identity_patterns_before_parsing(schema, pattern):
    value = contract(schema, pattern)
    with pytest.raises(ValueError):
        validate_acceptance_contract(value)
    with pytest.raises(ValueError):
        evaluate_response({'url': 'https://example.test/', 'task_kind': 'structure',
                           'acceptance_contract': value}, b'[Alpha](/item/1) $10')


@pytest.mark.parametrize('schema', ['named_link', 'linked_product'])
def test_shared_identity_validation_preserves_valid_single_capture(schema):
    value = contract(schema, r'/item/(\d+)')
    validate_acceptance_contract(value)
    assert evaluate_response({'url': 'https://example.test/', 'task_kind': 'structure',
                              'acceptance_contract': value}, b'[Alpha](/item/1) $10')['passed']
