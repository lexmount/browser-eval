"""Diagnostic anchors do not disable mandatory target-content patterns."""
import pytest

from lexbench_webfetch.benchmark import read, suite
from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt


def ibiblio():
    _, root = suite('access-restrictions')
    return next(c for c in read(root/'tasks.json')['cases'] if c['id'] == 'risk-diverse-192')


# Synthetic minimal target identity, not a complete source-page reference.
IDENTITY = ('ibiblio is a public digital library with freely available information '
            'about software, music and history at UNC.')


@pytest.mark.parametrize('answer,expected', [
    (IDENTITY, True),
    ('x', False),
    ('Explore Share Software Projects About ibiblio Donate About Help Blog Contact Privacy Logos', False),
    ('ibiblio Access denied. Verify you are human.', False),
    (IDENTITY.replace('public digital library', 'website'), False),
    (IDENTITY.replace('freely available information', 'paid services'), False),
    (IDENTITY.replace('software, music and history', 'other subjects'), False),
    (IDENTITY.replace('UNC', 'an unrelated institution'), False),
])
def test_ibiblio_diagnostic_anchors_still_require_declared_identity(answer, expected):
    case = ibiblio()
    assert case['acceptance_contract']['stable_text_role'] == 'diagnostic'
    metrics = score_attempt(case, answer.encode(), ok=True)
    assert task_passed(True, metrics) is expected
    assert all(row['pass'] for row in metrics['stable_page_pattern_results']) is expected
