"""Page labels retain equivalent representations without borrowing record text."""
import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt


CASE = {
    'id': 'page-label', 'url': 'https://example.test/', 'min_content_chars': 1,
    'acceptance_contract': {
        'version': 1, 'schema': 'named_link', 'minimum_records': 2,
        'hosts': ['example.test'], 'entry_path': r'/item/[0-9]+',
        'stable_text_anchors': [{'id': 'identity', 'alternatives': ['Example Site'],
                                 'match_scope': 'non_record_lines'}],
    },
}
HTML_RECORDS = ('<ul><li><a href="/item/1">First ordinary article</a></li>'
                '<li><a href="/item/2">Second ordinary article</a></li></ul>')
MD_RECORDS = '- [First ordinary article](/item/1)\n- [Second ordinary article](/item/2)'


@pytest.mark.parametrize('page,expected', [
    ('<a href="/"><img src="/logo.svg" alt="Example Site"></a>', True),
    ('<p>Example Site</p>', True),
    ('<a href="/help">Example Site</a>', True),
    ('<a href="/"><img src="/logo.svg" alt="Different Site"></a>', False),
    ('<img src="/logo.svg" alt="Example Site">', False),
    ('<a href="/item/3"><img src="/logo.svg" alt="Example Site"></a>', False),
    ('<a href="javascript:void(0)"><img src="/logo.svg" alt="Example Site"></a>', False),
    ('<div data-label="Example Site">Other label</div>', False),
    ('<a href="/Example Site">Other label</a>', False),
    ('<script>Example Site</script>', False),
    ('<div hidden><a href="/"><img alt="Example Site"></a></div>', False),
    ('<a href="/"><img style="display:none" alt="Example Site"></a>', False),
    ('<a style="visibility:hidden" href="/"><img alt="Example Site"></a>', False),
])
def test_html_non_record_page_identity(page, expected):
    metrics = score_attempt(CASE, ('<html><body>'+page+HTML_RECORDS+'</body></html>').encode(), ok=True)
    assert metrics['stable_complete_record_count'] == 2
    assert task_passed(True, metrics) is expected


@pytest.mark.parametrize('page,expected', [
    ('[![Example Site](/logo.svg)](/)', True),
    ('Example Site', True),
    ('[Example Site](/help)', True),
    ('[![Different Site](/logo.svg)](/)', False),
    ('![Example Site](/logo.svg)', False),
    ('[![Example Site](/logo.svg)](/item/3)', False),
    (r'[![Example Site](/logo.svg)](javascript:void\(0\))', False),
    ('<span data-label="Example Site">Other label</span>', False),
    ('<span hidden>Example Site</span>', False),
])
def test_markdown_non_record_page_identity(page, expected):
    metrics = score_attempt(CASE, (page+'\n\n'+MD_RECORDS).encode(), ok=True)
    assert metrics['stable_complete_record_count'] == 2
    assert task_passed(True, metrics) is expected


@pytest.mark.parametrize('navigation,expected', [
    ('<a href="/sports">Sports</a><a href="/guide">guide</a>', True),
    ('<a href="/sports">Sportsguide</a>', False),
    ('<a hidden href="/sports">Sports</a><a href="/guide">guide</a>', False),
])
def test_html_sibling_link_labels_retain_independent_boundaries(navigation, expected):
    case = {**CASE, 'acceptance_contract': {**CASE['acceptance_contract'],
            'stable_text_anchors': [{'id': 'sports', 'alternatives': ['Sports'],
                                    'match_scope': 'non_record_lines'}]}}
    answer = '<html><body><nav>'+navigation+'</nav>'+HTML_RECORDS+'</body></html>'
    metrics = score_attempt(case, answer.encode(), ok=True)
    assert task_passed(True, metrics) is expected


@pytest.mark.parametrize('page,expected', [
    (r'\<div class="layout"\> [Example Site](/help)', True),
    (r'\<div data-label="Other Site"\> [Other label](/help)', False),
    ('```html\n<div hidden>code example</div>\n```\n[Example Site](/help)', True),
])
def test_markdown_escaped_or_fenced_tags_do_not_select_whole_html_parser(page, expected):
    metrics = score_attempt(CASE, (page+'\n\n'+MD_RECORDS).encode(), ok=True)
    assert task_passed(True, metrics) is expected
