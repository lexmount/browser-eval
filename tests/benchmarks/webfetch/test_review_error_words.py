"""Generic access messages never replace checks of the requested content."""
import pytest

from lexbench_webfetch import benchmark
from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt


@pytest.fixture(scope='module')
def cases():
    result = {}
    for suite in ('webfetch', 'popular-sites'):
        data = benchmark.apply_annotation_revision(
            suite, benchmark.read(benchmark.suite(suite)[1] / 'tasks.json'),
            'review-550-20261023')
        result.update({case['id']: case for case in data['cases']})
    return result


def test_required_content_can_mention_verification(cases):
    case = cases['global-homepage-001-google-com']
    required = '\n'.join(anchor['alternatives'][0]
                         for anchor in case['acceptance_contract']['stable_text_anchors'])
    for text in (required, required + '\nCAPTCHA\nAccess Denied\nHuman Verification'):
        metrics = score_attempt(case, text.encode(), ok=True)
        assert not metrics['forbidden_requirement_hits']
        assert task_passed(True, metrics)


@pytest.mark.parametrize('case_id', ['challenge-02', 'challenge-04', 'challenge-06'])
def test_missing_search_results_fail_on_content(cases, case_id):
    case = cases[case_id]
    for text in ('Access Denied\nCAPTCHA\nVerify you are human',
                 'An unrelated page without any requested results.'):
        metrics = score_attempt(case, text.encode(), ok=True)
        assert not metrics['forbidden_requirement_hits']
        assert metrics['stable_task_errors']
        assert not task_passed(True, metrics)
