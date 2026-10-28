"""Historical comment qualification is separate from the current runtime."""
from .article_contract_fixtures import comment_labels
from copy import deepcopy
import pytest
from lexbench_webfetch.article_comments import validate_comment_contract, verify_article_comments
from lexbench_webfetch.article_qualification import qualify_article_comments

CONTRACT = {**comment_labels(), 'version': 2, 'minimum_comments': 1, 'record_selector': '.comment',
            'author_selector': '.author', 'body_selector': '.text', 'section_markers': ['Comments']}
REFERENCE = {'version': 1, 'case_id': 'article', 'url': 'https://example.test/article',
             'scope': 'initial_load_only', 'observation_complete': True,
             'context_id': 'historical', 'observed_on': '2026-10-19',
             'source_observation_counts': [1],
             'source_evidence': [{'path': 'capture.json', 'sha256': 'a' * 64, 'scope': 'complete_initial_page'}],
             'records': [{'id': '1', 'author': 'Alice', 'text': 'Actual comment.',
                          'parent_id': None, 'complete_initial_text': True}]}


def qualify(reference, contract=CONTRACT):
    return qualify_article_comments(contract, reference, case_id='article', url=REFERENCE['url'])


def test_historical_date_only_evidence_qualifies_without_runtime_clock():
    assert qualify(REFERENCE)['status'] == 'qualified'
    timestamped = deepcopy(REFERENCE)
    timestamped['observed_at'] = '2020-01-01T00:00:00Z'
    assert qualify(timestamped)['status'] == 'qualified'


@pytest.mark.parametrize('change', [
    {'version': True}, {'case_id': 'other'}, {'url': 'https://other.test'},
    {'scope': 'expanded'}, {'observation_complete': False}, {'context_id': ''},
    {'source_observation_counts': []}, {'source_observation_counts': None},
    {'source_observation_counts': [True]}, {'source_observation_counts': [2]},
    {'source_evidence': []}, {'observed_on': '2026-02-31'}, {'observed_at': 'bad'},
    {'records': None},
])
def test_malformed_or_inconsistent_historical_evidence_fails(change):
    ref = deepcopy(REFERENCE)
    ref.update(change)
    assert qualify(ref)['status'] != 'qualified'


@pytest.mark.parametrize('change', [
    {'id': ''}, {'author': ''}, {'author': '\u200b'}, {'text': ''},
    {'parent_id': 'missing'}, {'complete_initial_text': False},
    {'text': '', 'empty_body': True},
    {'text': '', 'empty_body': True, 'image_only': True, 'source_media_count': 0},
])
def test_invalid_historical_owned_records_fail(change):
    ref = deepcopy(REFERENCE)
    ref['records'][0].update(change)
    assert qualify(ref)['status'] != 'qualified'


def test_image_only_records_are_evidence_but_do_not_meet_text_floor():
    ref = deepcopy(REFERENCE)
    ref['records'][0].update(text='', empty_body=True, image_only=True, source_media_count=1)
    assert qualify(ref, None)['status'] == 'qualified'
    assert qualify(ref)['status'] == 'insufficient_comments'


def test_zero_evidence_qualifies_presence_review_without_fabricated_contract():
    ref = deepcopy(REFERENCE)
    ref.update(records=[], source_observation_counts=[0])
    assert qualify(ref, None)['status'] == 'qualified'
    assert qualify(ref)['status'] == 'insufficient_comments'


def test_repeated_ids_and_bad_source_binding_fail():
    ref = deepcopy(REFERENCE)
    ref['records'] *= 2
    ref['source_observation_counts'] = [2]
    assert qualify(ref)['status'] == 'invalid'
    ref = deepcopy(REFERENCE)
    ref['source_evidence'][0]['sha256'] = 'bad'
    assert qualify(ref)['status'] == 'invalid'


def test_runtime_explicitly_rejects_legacy_contracts_and_reference_argument():
    legacy = dict(CONTRACT, version=1, max_reference_age_seconds=900)
    legacy.pop('minimum_comments')
    with pytest.raises(ValueError):
        validate_comment_contract(legacy)
    with pytest.raises(ValueError):
        verify_article_comments('', legacy)
    with pytest.raises(TypeError):
        verify_article_comments('', CONTRACT, REFERENCE)
