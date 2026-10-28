"""Scope corrections select exact frozen bytes and preserve historical references."""
from copy import deepcopy
import hashlib

import pytest

from lexbench_webfetch.benchmark import public_task
from lexbench_webfetch.body_coverage import body_requirements, evaluate_body_requirements, tokens
from lexbench_webfetch.annotation_revisions import project_body_reference

CREDIT = 'Illustration credit: Research Studio.\n\n'
BODY = 'The team studied how birds navigate during migration. ' * 8
ENDING = 'Independent observations confirmed the measured migration routes.'
FOOTER = '\n\nShare this story and browse other projects.'
SOURCE = CREDIT + BODY + ENDING + FOOTER


def binding(text):
    return {'sha256': hashlib.sha256(text.encode()).hexdigest(), 'chars': len(text)}


def fixture():
    rule = body_requirements(SOURCE, structure_hint='whole_declared_component')
    rule.update(version=4, ending={'text': FOOTER.strip()}, minimum_floor=.8,
                minimum_region_passes=1)
    case = {'ground_truth_main_content': SOURCE, 'body_requirements': rule}
    projection = {'source_sha256': binding(SOURCE)['sha256'], 'source_chars': len(SOURCE),
        'regions': [{'id': 'body', 'label': 'Study and conclusion',
            'start': len(CREDIT), 'end': len(SOURCE) - len(FOOTER),
            'text_binding': binding(' '.join(tokens(BODY + ENDING))),
            'source_text_binding': binding(BODY + ENDING)}],
        'ending': {'start': len(CREDIT + BODY), 'end': len(SOURCE) - len(FOOTER),
                   'text_binding': binding(ENDING)}}
    return case, projection


def test_scope_correction_binds_the_real_conclusion_and_keeps_original_reference():
    case, projection = fixture()
    project_body_reference(case, projection, binding(SOURCE)['sha256'])
    assert case['ground_truth_main_content'] == SOURCE
    assert evaluate_body_requirements(BODY + ENDING, case['body_requirements'])['body_coverage_pass']
    assert not evaluate_body_requirements(BODY + FOOTER, case['body_requirements'])['body_coverage_pass']
    assert case['body_requirements']['regions'][0]['source_text'] == BODY + ENDING


def test_public_and_executable_projections_agree_without_publishing_source_text():
    case, projection = fixture()
    public = public_task(case)
    project_body_reference(case, projection, binding(SOURCE)['sha256'])
    project_body_reference(public, projection, binding(SOURCE)['sha256'])
    assert public_task(case) == public
    assert 'text' not in public['body_requirements']['ending']
    assert 'source_text' not in public['body_requirements']['regions'][0]


@pytest.mark.parametrize('change', [
    lambda p: p.update(source_sha256='0' * 64),
    lambda p: p.update(source_chars=True),
    lambda p: p.update(extra_field=True),
    lambda p: p['regions'][0].update(start=-1),
    lambda p: p['regions'][0].update(end=100000),
    lambda p: p['regions'][0].update(start=True),
    lambda p: p['regions'][0].update(label=''),
    lambda p: p['regions'][0]['text_binding'].update(sha256='0' * 64),
    lambda p: p['ending'].update(start=0, end=len(CREDIT), text_binding=binding(CREDIT.strip())),
    lambda p: p['regions'].append(deepcopy(p['regions'][0])),
    lambda p: p.update(regions=[]),
    lambda p: p.update(regions=p['regions'] * 6),
])
def test_invalid_projection_is_atomic(change):
    case, projection = fixture()
    before = deepcopy(case)
    change(projection)
    with pytest.raises(ValueError):
        project_body_reference(case, projection, binding(SOURCE)['sha256'])
    assert case == before


def test_changed_source_cannot_be_used_with_an_old_projection():
    case, projection = fixture()
    case['ground_truth_main_content'] += 'changed'
    before = deepcopy(case)
    with pytest.raises(ValueError, match='differs from frozen'):
        project_body_reference(case, projection, binding(SOURCE)['sha256'])
    assert case == before


def test_version_three_projects_static_prose_without_inventing_an_ending():
    case, projection = fixture()
    case['body_requirements']['version'] = 3
    del case['body_requirements']['ending']
    del projection['ending']
    public = public_task(case)
    project_body_reference(case, projection, binding(SOURCE)['sha256'])
    project_body_reference(public, projection, binding(SOURCE)['sha256'])
    assert case['body_requirements']['version'] == 3
    assert 'source_verification' not in case
    assert 'ending' not in case['body_requirements']
    assert public_task(case) == public
    assert case['ground_truth_main_content'] == SOURCE
    assert evaluate_body_requirements(BODY + ENDING, case['body_requirements'])['body_coverage_pass']
    assert not evaluate_body_requirements(CREDIT + FOOTER, case['body_requirements'])['body_coverage_pass']


def test_reviewed_mutable_body_becomes_required_without_changing_frozen_text():
    case, projection = fixture()
    del case['body_requirements']
    del projection['ending']
    case['mutable_body_policy'] = {'stable_annotation': {'content_kind': 'changing_public_body'}}
    public = public_task(case)
    project_body_reference(case, projection, binding(SOURCE)['sha256'])
    project_body_reference(public, projection, binding(SOURCE)['sha256'])
    assert public_task(case) == public
    assert 'mutable_body_policy' not in case
    assert 'source_verification' not in case
    assert 'source_verification' not in public
    assert case['ground_truth_main_content'] == SOURCE
    assert case['body_requirements']['minimum_floor'] == .8
    assert case['body_requirements']['minimum_region_passes'] == 1
    assert evaluate_body_requirements(BODY + ENDING, case['body_requirements'])['body_coverage_pass']
    assert not evaluate_body_requirements(CREDIT + FOOTER, case['body_requirements'])['body_coverage_pass']


@pytest.mark.parametrize('policy', [None, [], {}, {'content_kind': 'unknown'}])
def test_body_introduction_requires_an_existing_mutable_body_policy(policy):
    case, projection = fixture()
    del case['body_requirements']
    del projection['ending']
    case['mutable_body_policy'] = policy
    before = deepcopy(case)
    with pytest.raises(ValueError, match='Invalid body reference projection'):
        project_body_reference(case, projection, binding(SOURCE)['sha256'])
    assert case == before


def test_failed_body_introduction_keeps_original_policy():
    case, projection = fixture()
    del case['body_requirements']
    del projection['ending']
    case['mutable_body_policy'] = {'stable_annotation': {'content_kind': 'changing_public_body'}}
    projection['regions'][0]['text_binding']['sha256'] = '0' * 64
    before = deepcopy(case)
    with pytest.raises(ValueError, match='differs from reviewed binding'):
        project_body_reference(case, projection, binding(SOURCE)['sha256'])
    assert case == before


@pytest.mark.parametrize('version', [3, 4, 5])
def test_projection_cannot_change_whether_source_contract_has_an_ending(version):
    case, projection = fixture()
    case['body_requirements']['version'] = version
    if version == 3:
        del case['body_requirements']['ending']
    else:
        del projection['ending']
    before = deepcopy(case)
    with pytest.raises(ValueError, match='Invalid body reference projection'):
        project_body_reference(case, projection, binding(SOURCE)['sha256'])
    assert case == before
