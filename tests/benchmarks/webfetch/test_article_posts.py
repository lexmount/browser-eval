"""Historical post qualification never scores an empty runtime answer."""
from .article_contract_fixtures import post_labels
from copy import deepcopy
import pytest
from lexbench_webfetch.article_posts import validate_post_contract, verify_article_posts
from lexbench_webfetch.article_qualification import qualify_article_posts

CONTRACT = {**post_labels(), 'version': 2, 'hosts': ['example.test'], 'entry_path': r'/post/\d+',
            'html_record_selector': '.post', 'html_permalink_selector': 'a',
            'html_author_selector': '.author', 'html_body_selector': '.body',
            'minimum_posts': 1, 'allow_empty_outer_with_quotes': True}
REFERENCE = {'version': 1, 'case_id': 'feed', 'url': 'https://example.test/feed',
             'scope': 'initial_load_only', 'context_id': 'historic',
             'observed_at': '2020-01-01T00:00:00Z', 'source_observation_counts': [1, 1],
             'records': [{'url': 'https://example.test/post/1', 'author': 'Alice',
                          'body_parts': [{'kind': 'post', 'text': 'A complete displayed post body.',
                                          'complete_initial_text': True}]}]}


def qualify(ref, contract=CONTRACT):
    return qualify_article_posts(contract, ref, case_id='feed', url=REFERENCE['url'])


def test_historical_posts_need_no_current_clock_or_answer():
    assert qualify(REFERENCE)['status'] == 'qualified'


@pytest.mark.parametrize('change', [
    {'version': True}, {'case_id': 'other'}, {'url': 'https://other.test'},
    {'scope': 'expanded'}, {'context_id': ''}, {'observed_at': 'bad'},
    {'source_observation_counts': []}, {'source_observation_counts': [1]},
    {'source_observation_counts': [1, 2]}, {'source_observation_counts': [True, 1]},
    {'records': []}, {'records': None},
])
def test_bad_post_observation_fails(change):
    ref = deepcopy(REFERENCE)
    ref.update(change)
    assert qualify(ref)['status'] != 'qualified'


@pytest.mark.parametrize('change', [
    {'url': 'https://foreign.test/post/1'}, {'url': 'https://example.test:bad/post/1'},
    {'author': ''}, {'body_parts': []},
    {'body_parts': [{'kind': 'post', 'text': 'Hidden body', 'complete_initial_text': False}]},
    {'body_parts': [{'kind': 'post', 'text': '', 'complete_initial_text': True}]},
])
def test_bad_post_owned_fields_fail(change):
    ref = deepcopy(REFERENCE)
    ref['records'][0].update(change)
    assert qualify(ref)['status'] != 'qualified'


def test_quote_only_observation_requires_explicit_empty_outer_and_quote_author():
    ref = deepcopy(REFERENCE)
    ref['records'][0]['body_parts'] = [
        {'kind': 'post', 'text': '', 'empty_outer_text': True, 'complete_initial_text': True},
        {'kind': 'quote', 'author': 'Bob', 'text': 'Complete attributed quoted text.', 'complete_initial_text': True}]
    assert qualify(ref)['status'] == 'qualified'
    del ref['records'][0]['body_parts'][1]['author']
    assert qualify(ref)['status'] == 'invalid'


def test_ranges_cannot_omit_text_and_duplicate_posts_fail():
    ref = deepcopy(REFERENCE)
    ref['records'][0]['body_parts'][0]['source_line_ranges'] = [[2, 2]]
    assert qualify(ref)['status'] == 'invalid'
    ref = deepcopy(REFERENCE)
    ref['records'] *= 2
    ref['source_observation_counts'] = [2, 2]
    assert qualify(ref)['status'] == 'invalid'
    assert qualify(REFERENCE, dict(CONTRACT, minimum_posts=2))['status'] == 'invalid'


def test_runtime_rejects_legacy_post_contract_and_reference_argument():
    legacy = dict(CONTRACT, version=1, required_ratio=1, max_reference_age_seconds=900)
    legacy.pop('minimum_posts')
    with pytest.raises(ValueError):
        validate_post_contract(legacy)
    with pytest.raises(ValueError):
        verify_article_posts('', legacy, url=REFERENCE['url'])
    with pytest.raises(TypeError):
        verify_article_posts('', CONTRACT, REFERENCE, url=REFERENCE['url'])
