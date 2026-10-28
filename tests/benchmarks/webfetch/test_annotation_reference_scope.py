"""A main-body reference qualifies body edits, never unrelated page changes."""
from copy import deepcopy
import pytest
from lexbench_webfetch.benchmark import check_revision_references
from lexbench_webfetch.body_coverage import body_requirements


def fixture():
    text = 'The scientist measured distant stars and compared their changing brightness. ' * 10
    ending = 'A new independent observation should confirm these measurements.'
    body = body_requirements(text + ending, structure_hint='whole_declared_component')
    body.update(version=5, ending={'text': ending}, minimum_floor=.8, minimum_region_passes=1)
    case = {'id': 'article', 'url': 'https://example.test/article',
        'ground_truth_main_content': text + ending, 'body_requirements': body,
        'acceptance_contract': {'version': 1, 'schema': 'body_text', 'minimum_records': 0,
            'stable_text_anchors': [{'id': 'site-header', 'alternatives': ['Site navigation']}]} }
    review = {'changes': [{'case_id': 'article', 'sources': [{'path': 'dataset.json'}],
                          'changes': [{'path': '/body_requirements/version', 'before': 4, 'after': 5}]}]}
    return {'cases': [case]}, review


def test_body_reference_pass_is_explicitly_scoped(tmp_path):
    dataset, review = fixture()
    result = check_revision_references(dataset, review, tmp_path)
    assert result['changed_rules_complete']
    assert not result['complete']
    assert result['cases'][0]['verification_scope'] == 'body_only'
    assert not result['cases'][0]['error_page_passed']


def test_changed_body_still_must_accept_its_bound_reference(tmp_path):
    dataset, review = fixture()
    dataset['cases'][0]['body_requirements']['ending']['text'] = 'An unrelated closing statement is mandatory now.'
    result = check_revision_references(dataset, review, tmp_path)
    assert result['cases'][0]['status'] == 'failed'
    assert not result['changed_rules_complete']


def test_page_edits_cannot_use_a_body_only_reference(tmp_path):
    dataset, review = fixture()
    original = deepcopy(dataset)
    review['changes'][0]['changes'].append({'path': '/acceptance_contract/stable_text_anchors/0'})
    result = check_revision_references(dataset, review, tmp_path)
    assert result['cases'][0]['status'] == 'failed'
    assert not result['changed_rules_complete']
    assert dataset == original


def test_full_reference_answer_still_qualifies_page_edits(tmp_path):
    dataset, review = fixture()
    case = dataset['cases'][0]
    case['reference_answer'] = 'Site navigation\n' + case['ground_truth_main_content']
    review['changes'][0]['changes'].append({'path': '/acceptance_contract/stable_text_anchors/0'})
    result = check_revision_references(dataset, review, tmp_path)
    assert result['complete'] and result['changed_rules_complete']
    assert result['cases'][0]['verification_scope'] == 'full_task'


@pytest.mark.parametrize('phrase,passed', [('measured migration', False), ('changing brightness', True)])
def test_appended_stable_requirement_must_occur_in_the_bound_body(tmp_path, phrase, passed):
    dataset, review = fixture()
    anchors = dataset['cases'][0]['acceptance_contract']['stable_text_anchors']
    before = deepcopy(anchors)
    anchors.append({'id': 'moved-credit', 'alternatives': [phrase]})
    review['changes'][0]['changes'].append({'path': '/acceptance_contract/stable_text_anchors',
                                            'before': before, 'after': deepcopy(anchors)})
    result = check_revision_references(dataset, review, tmp_path)
    assert result['changed_rules_complete'] is passed
    assert not result['complete']
    if passed:
        assert result['cases'][0]['verification_scope'] == 'body_and_added_stable_text'


def test_replacing_old_stable_requirements_needs_full_page_evidence(tmp_path):
    dataset, review = fixture()
    anchors = dataset['cases'][0]['acceptance_contract']['stable_text_anchors']
    before = deepcopy(anchors)
    anchors[0]['alternatives'] = ['A missing navigation label']
    review['changes'][0]['changes'].append({'path': '/acceptance_contract/stable_text_anchors',
                                            'before': before, 'after': deepcopy(anchors)})
    result = check_revision_references(dataset, review, tmp_path)
    assert not result['changed_rules_complete']


@pytest.mark.parametrize('full_page', [True, False])
def test_later_review_source_can_upgrade_body_scope(tmp_path, full_page):
    import hashlib
    dataset, review = fixture()
    text = dataset['cases'][0]['ground_truth_main_content']
    raw = (('Site navigation\n' if full_page else '') + text).encode()
    (tmp_path / 'page.txt').write_bytes(raw)
    review['changes'][0]['sources'].append(
        {'path': 'page.txt', 'sha256': hashlib.sha256(raw).hexdigest()})
    result = check_revision_references(dataset, review, tmp_path)
    assert result['changed_rules_complete']
    assert result['complete'] is full_page
    assert result['cases'][0]['verification_scope'] == ('full_task' if full_page else 'body_only')
