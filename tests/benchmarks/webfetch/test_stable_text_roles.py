"""Task-declared excluded controls cannot substitute for ordinary page text."""
import pytest

from lexbench_webfetch.scoring import score_attempt
from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.stable_tasks import validate_acceptance_contract


def contract():
    return {'version': 1, 'schema': 'body_text', 'minimum_records': 0,
            'stable_text_anchors': [{'id': 'live', 'alternatives': ['Live']}],
            'text_exclusions': {'html_selectors': ['button', '[role="button"]'],
                                'text_patterns': [r'^\s*\d+:\d{2}\s*/\s*Live\s*[•·]?\s*Watch full video\b']}}


@pytest.mark.parametrize('answer,expected', [
    ('<main>Live</main><button>Pause</button>', True),
    ('<main>Other</main><button>Live</button>', False),
    ('<main>Other</main><div role="button">Live</div>', False),
    ('0:00\n\n/\n\nLive\n\n•Watch full video', False),
    ('Live\n\n0:00\n/\nLive\n•Watch full video', True),
    ('[Live](https://example.com/live)', True),
])
def test_ordinary_text_and_controls_use_the_same_shared_scorer(answer, expected):
    case = {'id': 'ordinary', 'url': 'https://example.com/', 'task_kind': 'structure',
            'content_score_applicable': False, 'acceptance_contract': contract()}
    metrics = score_attempt(case, answer.encode(), ok=True)
    assert task_passed(True, metrics) is expected


def test_exclusion_config_fails_closed():
    for exclusions in ({'unknown': []}, {'text_patterns': ['[']}, {'html_selectors': ['[']},
                       {'text_patterns': 'Live'}):
        with pytest.raises(Exception):
            validate_acceptance_contract({**contract(), 'text_exclusions': exclusions})


def test_full_page_scope_requires_body_and_accepts_navigation_as_additional_content():
    from lexbench_webfetch.body_coverage import body_requirements
    c = {'id': 'coverage', 'url': 'https://example.test/', 'task_kind': 'body',
         'task_prompt': '获取目标正文及导航、页脚中登记的稳定文字。',
         'body_requirements': body_requirements('This specific article explains the observed retrieval contract.'),
         'acceptance_contract': {'version': 1, 'schema': 'body_text', 'minimum_records': 0,
            'content_scope': 'full_page_stable_coverage', 'stable_text_anchors': [
                {'id': 'home', 'alternatives': ['Home'], 'source_location': 'nav > a'},
                {'id': 'terms', 'alternatives': ['Terms'], 'source_location': 'footer > a'}]}}
    body = 'This specific article explains the observed retrieval contract.'
    assert task_passed(True, score_attempt(c, ('Home\n'+body+'\nTerms').encode(), ok=True))
    assert not task_passed(True, score_attempt(c, b'Home\nTerms', ok=True))
    with pytest.raises(ValueError, match='target records or body regions'):
        score_attempt({**c, 'body_requirements': {}}, b'Home\nTerms', ok=True)
    with pytest.raises(ValueError, match='cannot supply'):
        validate_acceptance_contract({**c['acceptance_contract'], 'content_scope': 'target_content'})


def test_explicit_target_scope_cannot_borrow_the_same_text_from_navigation():
    contract = {'version': 1, 'schema': 'body_text', 'minimum_records': 0,
                'content_scope': 'target_content', 'stable_text_anchors': [
                    {'id': 'target', 'alternatives': ['Target article'], 'source_location': 'main > p'}]}
    case = {'id': 'scope', 'url': 'https://example.test/', 'task_kind': 'body', 'acceptance_contract': contract}
    assert not task_passed(True, score_attempt(case, b'<nav>Target article</nav><main>Other</main>', ok=True))
    assert task_passed(True, score_attempt(case, b'<nav>Other</nav><main>Target article</main>', ok=True))
