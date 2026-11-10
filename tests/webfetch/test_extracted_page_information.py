"""Equivalent extracted fields count without inventing page structure or prose."""
from copy import deepcopy

import pytest

from lexbench_webfetch.scoring import score_attempt
from lexbench_webfetch.metrics import task_passed


def case():
    return {'id': 'synthetic', 'url': 'https://example.test/essay',
            'task_kind': 'body', 'expected_any': [], 'min_content_chars': 20,
            'acceptance_contract': {'version': 1, 'schema': 'body_text',
                'minimum_records': 0, 'stable_text_anchors': [
                    {'id': 'date', 'alternatives': ['May 2026']}]}}


def score(c, information, body=b'This is the complete synthetic essay body.'):
    result = score_attempt(c, body, ok=True,
                           metadata={'answer_format': 'plain_text', 'page_information': information})
    return task_passed(True, result, 'body'), result


@pytest.mark.parametrize('information,passed', [
    ({'published_at': 'May 2026'}, True),
    ({'published_at': 'May 2025'}, False),
    ({'published_at': ['May', '2026']}, False),
    ({'author': 'Jane Example'}, False),
])
def test_independent_extracted_fact_values(information, passed):
    assert score(case(), information)[0] is passed


@pytest.mark.parametrize('body', [b'', b'Tiny'])
def test_information_cannot_supply_required_body_length(body):
    assert score(case(), {'published_at': 'May 2026'}, body)[0] is False


@pytest.mark.parametrize('scope', [
    {'content_scope': 'target_content'},
    {'text_exclusions': {'text_patterns': ['May 2026']}},
])
def test_extracted_fields_cannot_bypass_excluded_regions(scope):
    c = case()
    c['acceptance_contract'].update(scope)
    assert score(c, {'published_at': 'May 2026'})[0] is False


@pytest.mark.parametrize('field', ['keywords', 'debug', 'url', 'raw_html'])
def test_arbitrary_metadata_is_not_extracted_page_information(field):
    with pytest.raises(ValueError, match='page information'):
        score(case(), {field: 'May 2026'})


def test_extracted_title_is_distinct_from_document_metadata():
    c = case()
    c['source_benchmark'] = {'title_contract': {'version': 1,
        'alternatives': ['A Useful Essay'], 'html_selector': 'h1'}}
    info = {'published_at': 'May 2026', 'title': 'A Useful Essay'}
    assert score(c, info)[0] is True
    assert score(c, {**info, 'title': 'A Different Essay'})[0] is False
    result = score_attempt(c, b'This is the complete synthetic essay body.', ok=True,
        title='A Useful Essay', metadata={'answer_format': 'plain_text',
            'page_information': {'published_at': 'May 2026'}})
    assert result['article_title_pass'] is False
    excluded = deepcopy(c)
    excluded['source_benchmark']['title_contract']['excluded_html_selectors'] = ['aside']
    assert score(excluded, info)[0] is False


def test_page_pattern_can_read_an_independent_title_without_joining_fields():
    c = case()
    c['acceptance_contract']['page_patterns'] = ['Useful Essay']
    assert score(c, {'published_at': 'May 2026', 'title': 'Useful Essay'})[0] is True
    assert score(c, {'published_at': 'May 2026', 'title': 'Useful', 'author': 'Essay'})[0] is False


def test_explicit_body_fact_scope_does_not_accept_supplemental_fields():
    c = case()
    c['required_facts'] = [{'id': 'date', 'alternatives': ['May 2026'], 'match_scope': 'body'}]
    assert score(c, {'published_at': 'May 2026'})[0] is False
    del c['required_facts'][0]['match_scope']
    assert score(c, {'published_at': 'May 2026'})[0] is True


def test_extracted_information_does_not_create_records():
    c = case()
    c['acceptance_contract'] = {'version': 1, 'schema': 'named_link',
        'minimum_records': 2, 'hosts': ['example.test'], 'entry_path': '/story/',
        'stable_text_anchors': []}
    assert score(c, {'title': 'First story', 'description': 'Second story'})[0] is False


def test_plain_output_and_structured_output_get_the_same_information_credit():
    c = case()
    plain = score_attempt(c, b'May 2026\nThis is the complete synthetic essay body.', ok=True)
    structured_pass, structured = score(c, {'published_at': 'May 2026'})
    assert structured_pass is task_passed(True, plain, 'body') is True
    assert structured['stable_anchor_hits'] == plain['stable_anchor_hits']


def test_malformed_heading_string_cannot_become_character_titles():
    c = case()
    c['source_benchmark'] = {'title_contract': {'version': 1,
        'alternatives': ['A'], 'html_selector': 'h1'}}
    for metadata in ({'page_structure': {'headings': 'ABC'}},
                     {'page_information': {'headings': 'ABC'}}):
        with pytest.raises(ValueError, match='headings must be a list'):
            score_attempt(c, b'The article was published May 2026.', ok=True, metadata=metadata)


def test_returned_headings_respect_title_exclusions():
    c = case()
    c['source_benchmark'] = {'title_contract': {'version': 1,
        'alternatives': ['A Useful Essay'], 'html_selector': 'h1'}}
    metadata = {'answer_format': 'plain_text', 'page_structure': {'headings': ['A Useful Essay']}}
    body = b'This is the complete synthetic essay body.'
    assert score_attempt(c, body, ok=True, metadata=metadata)['article_title_pass'] is True
    c['source_benchmark']['title_contract']['excluded_html_selectors'] = ['aside']
    assert score_attempt(c, body, ok=True, metadata=metadata)['article_title_pass'] is False
