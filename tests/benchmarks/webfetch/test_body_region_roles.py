from copy import deepcopy

import pytest

from lexbench_webfetch.benchmark import public_task
from lexbench_webfetch.annotation_revisions import move_regions_to_stable_text
from lexbench_webfetch.body_coverage import evaluate_body_requirements
from lexbench_webfetch.stable_tasks import evaluate_stable_task


PROSE = 'The archive preserves research and makes publications freely available. ' * 5
LABELS = 'News\nAll News'


def fixture():
    return {'url': 'https://example.test/archive', 'body_requirements': {
        'version': 3, 'unit': 'region_compact_hexagram_recall',
        'minimum_coverage': .8, 'minimum_floor': .8, 'minimum_region_passes': 2,
        'regions': [{'id': 'prose', 'text': PROSE}, {'id': 'nav', 'text': LABELS}]},
        'acceptance_contract': {'version': 1, 'schema': 'body_text',
            'minimum_records': 0, 'stable_text_role': 'required',
            'stable_text_anchors': [{'id': 'identity', 'alternatives': ['archive']}]}}


def moves():
    return [{'region_id': 'nav', 'anchor_id': 'required-news', 'text': LABELS}]


def test_navigation_becomes_required_text_and_prose_stays_required():
    case = fixture()
    move_regions_to_stable_text(case, moves())
    assert evaluate_body_requirements(PROSE, case['body_requirements'])['body_coverage_pass']
    assert not evaluate_body_requirements(LABELS, case['body_requirements'])['body_coverage_pass']
    assert evaluate_stable_task(PROSE + LABELS, case['acceptance_contract'])['stable_task_pass']
    assert not evaluate_stable_task(PROSE, case['acceptance_contract'])['stable_task_pass']
    assert case['body_requirements']['minimum_region_passes'] == 1


def test_public_and_executable_roles_agree():
    case = fixture()
    public = public_task(case)
    move_regions_to_stable_text(case, moves())
    move_regions_to_stable_text(public, moves())
    assert public_task(case) == public


@pytest.mark.parametrize('bad', [
    [], [{'region_id': 'missing', 'anchor_id': 'new', 'text': LABELS}],
    [{'region_id': 'nav', 'anchor_id': 'identity', 'text': LABELS}],
    [{'region_id': 'nav', 'anchor_id': 'new', 'text': 'Unreviewed replacement'}],
    moves() * 2,
    moves() + [{'region_id': 'prose', 'anchor_id': 'no-prose', 'text': PROSE}],
])
def test_invalid_moves_fail_atomically(bad):
    case = fixture()
    original = deepcopy(case)
    with pytest.raises(ValueError):
        move_regions_to_stable_text(case, bad)
    assert case == original


@pytest.mark.parametrize('field,value', [('version', 4), ('minimum_floor', .6),
                                      ('minimum_region_passes', 1)])
def test_non_strict_or_ending_contracts_are_not_reclassified(field, value):
    case = fixture()
    case['body_requirements'][field] = value
    with pytest.raises(ValueError):
        move_regions_to_stable_text(case, moves())


def test_merging_required_short_labels_into_long_prose_can_hide_their_loss():
    merged = fixture()['body_requirements']
    merged['regions'] = [{'id': 'merged', 'text': PROSE + LABELS}]
    merged['minimum_region_passes'] = 1
    # The proposed merge changes a mandatory label into a small fraction of
    # the body allowance. A complete prose-only answer then passes at 80%.
    assert evaluate_body_requirements(PROSE, merged)['body_coverage_pass']
    case = fixture()
    move_regions_to_stable_text(case, moves())
    assert not evaluate_stable_task(PROSE, case['acceptance_contract'])['stable_task_pass']
