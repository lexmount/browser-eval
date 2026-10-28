"""Bodies, stable labels and article components share native disclosure visibility."""
import pytest
from lexbench_webfetch.body_coverage import body_requirements
from lexbench_webfetch.dynamic_lists import visible_html_text
from lexbench_webfetch.scoring import score_attempt

TEXT = 'Complete authored explanation of the mountain expedition and its route.'


@pytest.mark.parametrize("wrapper", ["<p>{}</p>", "{}"] )
def test_closed_body_does_not_supply_reference_coverage(wrapper):
    contract = body_requirements(TEXT)
    closed = '<details><summary>Overview</summary>' + wrapper.format(TEXT) + '</details>'
    opened = closed.replace('<details>', '<details open>')
    task = {'id': 'body-visibility', 'url': 'https://example.test/article',
            'body_requirements': contract, 'min_content_chars': 1}
    assert not score_attempt(task, closed.encode(), ok=True)['body_coverage_pass']
    assert score_attempt(task, opened.encode(), ok=True)['body_coverage_pass']


def test_only_first_direct_summary_survives_closed_ancestor():
    html = ('<details><summary>Shown label</summary><summary>Second hidden label</summary>'
            '<details open><summary>Nested hidden label</summary>Hidden text</details></details>')
    assert visible_html_text(html).strip() == 'Shown label'


def test_registered_visible_control_and_disclosure_verdicts():
    from lexbench_webfetch.scoring_regressions import load_scoring_regressions
    controls = load_scoring_regressions()
    from lexbench_webfetch.metrics import task_passed
    for key, row in controls.items():
        if key.startswith(('visible-control-', 'native-disclosure-')):
            metrics = score_attempt(row['case'], row['artifact'].encode(), ok=row['ok'])
            assert task_passed(row['ok'], metrics) == row['expected_task_pass'], key
