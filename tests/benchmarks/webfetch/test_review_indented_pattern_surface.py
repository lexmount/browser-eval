"""Visible-pattern and page-pattern scopes render indented literals once."""
import pytest

from lexbench_webfetch.evaluation import evaluate_response
from lexbench_webfetch.stable_tasks import evaluate_stable_task


CASES = [
    ('plain-block', '    Required label', r'^Required label$', True),
    ('wrong-label', '    Other label', r'^Required label$', False),
    ('inner-indent', '        Required label', r'^ {4}Required label$', True),
    ('lost-inner-indent', '        Required label', r'^Required label$', False),
    ('tabs', '\tif ready:\n\t\tprint(value)', r'^if ready:\n {4}print\(value\)$', True),
    ('list-continuation', '- item\n\n      Required label', r'^Required label$', True),
    ('quoted-block', '>     Required label', r'^Required label$', True),
    ('list-first-line', '-     Required label', r'^Required label$', True),
    ('literal-link', '    [Order](https://example.test/ "token")',
     r'^\[Order\]\(https://example\.test/ "token"\)$', True),
    ('interpreted-link', '    [Order](https://example.test/ "token")', r'^Order$', False),
    ('literal-image', '    ![diagram](example.png)', r'^!\[diagram\]\(example\.png\)$', True),
    ('block-before-prose', '    Required label\n\nAfter', r'^Required label$', True),
    ('prose-after-block', '    Required label\n\nAfter', r'^After$', True),
    ('ordinary-soft-wrap', 'Paragraph\n    continuation', r'^ {4}continuation$', True),
    ('not-a-code-block', 'Paragraph\n    continuation', r'^continuation$', False),
]


def _case(pattern, mode):
    case = {'url': 'https://example.test/'}
    if mode == 'page':
        case['acceptance_contract'] = {'version': 1, 'schema': 'body_text', 'minimum_records': 0,
                                       'page_patterns': ['(?m)' + pattern], 'stable_text_anchors': []}
    else:
        case['required_patterns'] = [{'id': 'visible-code', 'pattern': '(?m)' + pattern,
                                      'match_scope': 'visible_text'}]
    return case


@pytest.mark.parametrize('mode', ['visible', 'page'])
@pytest.mark.parametrize('identifier,text,pattern,passed', CASES)
def test_indented_literal_pattern_surfaces_preserve_displayed_text(identifier, text, pattern, passed, mode):
    case = _case(pattern, mode)
    if mode == 'page':
        assert evaluate_stable_task(text, case['acceptance_contract'])['stable_task_pass'] is passed
    result = evaluate_response(case, text.encode())
    assert result['passed'] is passed
    assert result['metrics']['content_chars'] == len(text)


@pytest.mark.parametrize('pattern,passed', [(r'^01\+20;02\+30;$', True), (r'^\+20;\+30;$', False)])
def test_code_compact_keeps_indented_numeric_operands(pattern, passed):
    case = {'url': 'https://example.test/', 'required_patterns': [
        {'id': 'code', 'pattern': pattern, 'match_scope': 'code_compact'}]}
    assert evaluate_response(case, b'    01 +20;\n    02 +30;')['passed'] is passed


@pytest.mark.parametrize('mode', ['visible', 'page'])
@pytest.mark.parametrize('pattern,passed', [(r'^`Required label`$', True), (r'^Required label$', False)])
def test_rendered_html_literal_text_is_not_markdown_formatting(mode, pattern, passed):
    case = _case(pattern, mode)
    text = '<pre>`Required label`</pre>'
    if mode == 'page':
        assert evaluate_stable_task(text, case['acceptance_contract'])['stable_task_pass'] is passed
    assert evaluate_response(case, text.encode(), metadata={'answer_format': 'html'})['passed'] is passed


SEMICOLONS = []
for native in [False, True]:
    record = '<main><a href="/item&semi;">Story</a></main>' if native else '[Story](/item&semi;)'
    for independent, passed in [(False, False), (True, True)]:
        heading = ('<h1>Story</h1>\n' if native else '# Story\n\n') if independent else ''
        SEMICOLONS.append((native, independent, heading + record, passed))


def _semicolon_case():
    return {'url': 'https://example.test/', 'acceptance_contract': {
        'version': 1, 'schema': 'named_link', 'minimum_records': 1,
        'hosts': ['example.test'], 'entry_path': r'/item;', 'stable_text_anchors': [
            {'id': 'independent-page-title', 'alternatives': ['Story'], 'match_scope': 'non_record_lines'}]}}


@pytest.mark.parametrize('native,independent,text,passed', SEMICOLONS)
def test_non_record_masks_keep_the_same_terminal_semicolon_identity(native, independent, text, passed):
    case = _semicolon_case()
    direct = evaluate_stable_task(text, case['acceptance_contract'], case['url'])
    assert direct['stable_record_count'] == 1
    assert direct['stable_task_pass'] is passed
    assert evaluate_response(case, text.encode())['passed'] is passed


def scoring_controls():
    exported = {'plain-block', 'wrong-label', 'inner-indent', 'lost-inner-indent',
                'literal-link', 'interpreted-link', 'block-before-prose', 'prose-after-block'}
    for mode in ['visible', 'page']:
        for identifier, text, pattern, passed in CASES:
            if identifier not in exported:
                continue
            yield {'id': f'runtime13-{mode}-{identifier}', 'category': 'format-normalization',
                   'failure_mode': 'false-negative' if passed else 'false-positive',
                   'root_cause': 'indented_literal_pattern_surface_preserves_container_and_code_boundaries',
                   'case': _case(pattern, mode), 'artifact': text, 'ok': True, 'expected_task_pass': passed,
                   'expected_metrics': {'content_chars': len(text)}}
    for native, independent, text, passed in SEMICOLONS:
        yield {'id': f'runtime13-semicolon-{native}-{independent}', 'category': 'structured-extraction',
               'failure_mode': 'false-negative' if passed else 'false-positive',
               'root_cause': 'non_record_mask_and_extractor_share_exact_returned_url_joining',
               'case': _semicolon_case(), 'artifact': text, 'ok': True, 'expected_task_pass': passed,
               'expected_metrics': {'stable_record_count': 1}}
    for mode in ['visible', 'page']:
        for pattern, passed in [(r'^`Required label`$', True), (r'^Required label$', False)]:
            yield {'id': f'runtime13-html-literal-{mode}-{passed}', 'category': 'format-normalization',
                   'failure_mode': 'false-negative' if passed else 'false-positive',
                   'root_cause': 'rendered_html_pattern_text_is_not_reparsed_as_markdown',
                   'case': _case(pattern, mode), 'artifact': '<pre>`Required label`</pre>',
                   'metadata': {'answer_format': 'html'}, 'ok': True, 'expected_task_pass': passed}
