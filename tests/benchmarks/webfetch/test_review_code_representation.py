"""Code matching preserves literal spelling and decodes only rendered HTML."""
import re

import pytest

from lexbench_webfetch.evaluation import evaluate_response


CASES = [
    ('markdown-entity', '```python\nx = "&lt;"\n```', 'markdown', 'x="&lt;"', True),
    ('markdown-decoded', '```python\nx = "&lt;"\n```', 'markdown', 'x="<"', False),
    ('markdown-nested-entity', '~~~python\nx = "&amp;lt;"\n~~~', 'markdown', 'x="&amp;lt;"', True),
    ('markdown-nested-decoded', '~~~python\nx = "&amp;lt;"\n~~~', 'markdown', 'x="&lt;"', False),
    ('plain-tags', 'x = "<b>value</b>"', 'plain_text', 'x="<b>value</b>"', True),
    ('plain-stripped-tags', 'x = "<b>value</b>"', 'plain_text', 'x="value"', False),
    ('plain-entity', 'x = "&lt;"', 'plain_text', 'x="&lt;"', True),
    ('plain-decoded', 'x = "&lt;"', 'plain_text', 'x="<"', False),
    ('html-decoded', '<pre>x = "&lt;"</pre>', 'html', 'x="<"', True),
    ('html-raw-entity', '<pre>x = "&lt;"</pre>', 'html', 'x="&lt;"', False),
    ('html-once', '<pre>x = "&amp;lt;"</pre>', 'html', 'x="&lt;"', True),
    ('html-twice', '<pre>x = "&amp;lt;"</pre>', 'html', 'x="<"', False),
    ('html-literal-fence', '<pre>```\nx = "&lt;"\n```</pre>', 'html', '```x="<"```', True),
    ('html-fence-raw-entity', '<pre>```\nx = "&lt;"\n```</pre>', 'html', '```x="&lt;"```', False),
    ('markdown-literal-tags', '```python\nx = "<b>value</b>"\n```', 'markdown', 'x="<b>value</b>"', True),
    ('markdown-stripped-tags', '```python\nx = "<b>value</b>"\n```', 'markdown', 'x="value"', False),
    ('plain-fence', '```\nx = 1\n```', 'plain_text', '```x=1```', True),
    ('plain-interpreted-fence', '```\nx = 1\n```', 'plain_text', 'x=1', False),
    ('plain-listing', '01 x = 1\n02 y = 2', 'plain_text', '01x=102y=2', True),
    ('plain-stripped-listing', '01 x = 1\n02 y = 2', 'plain_text', 'x=1y=2', False),
    ('markdown-listing', '01 x = 1\n02 y = 2', 'markdown', 'x=1y=2', True),
    ('markdown-raw-listing', '01 x = 1\n02 y = 2', 'markdown', '01x=102y=2', False),
    ('markdown-rendered-entity', 'x = "&lt;"', 'markdown', 'x="<"', True),
    ('markdown-once', 'x = "&amp;lt;"', 'markdown', 'x="&lt;"', True),
    ('markdown-twice', 'x = "&amp;lt;"', 'markdown', 'x="<"', False),
    ('markdown-escaped-tags', 'x = "&lt;b&gt;value&lt;/b&gt;"', 'markdown', 'x="<b>value</b>"', True),
    ('markdown-reparsed-tags', 'x = "&lt;b&gt;value&lt;/b&gt;"', 'markdown', 'x="value"', False),
    ('html-rendered-tags', '<pre>x = "<b>value</b>"</pre>', 'html', 'x="value"', True),
    ('html-unrendered-tags', '<pre>x = "<b>value</b>"</pre>', 'html', 'x="<b>value</b>"', False),
    ('plain-code-container', '<pre>x = 1</pre>', 'plain_text', '<pre>x=1</pre>', True),
    ('plain-interpreted-container', '<pre>x = 1</pre>', 'plain_text', 'x=1', False),
    ('html-inline-code', '<code>x = "&amp;lt;"</code>', 'html', 'x="&lt;"', True),
    ('html-inline-double-decode', '<code>x = "&amp;lt;"</code>', 'html', 'x="<"', False),
    ('html-unfenced-code', '<span>x = "&amp;lt;"</span>', 'html', 'x="&lt;"', True),
    ('html-unfenced-double-decode', '<span>x = "&amp;lt;"</span>', 'html', 'x="<"', False),
    ('html-code-one-owner', '<code>x = 1</code>', 'html', 'x=1', True),
    ('html-code-separate-owners', '<code>x =</code><p>Explanation</p><code>1</code>', 'html', 'x=1', False),
    ('html-document-prose', '<html><body><p>x =</p><p>1</p></body></html>', 'html', 'x=1', False),
]


def _case(expected):
    return {'url': 'https://example.test/', 'required_patterns': [
        {'id': 'source', 'match_scope': 'code_compact', 'pattern': '^' + re.escape(expected) + '$'}]}


@pytest.mark.parametrize('identifier,text,representation,expected,passed', CASES)
def test_public_code_matching_obeys_the_declared_representation(identifier, text, representation, expected, passed):
    result = evaluate_response(_case(expected), text.encode(), metadata={'answer_format': representation})
    assert result['passed'] is passed


def scoring_controls():
    for identifier, text, representation, expected, passed in CASES:
        yield {'id': 'runtime9-' + identifier, 'category': 'answer-surface',
               'failure_mode': 'false-negative' if passed else 'false-positive',
               'root_cause': 'code_representation_preserves_literal_spelling_and_single_html_decode',
               'case': _case(expected), 'artifact': text, 'metadata': {'answer_format': representation},
               'ok': True, 'expected_task_pass': passed}
