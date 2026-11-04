import pytest

from lexbench_webfetch.reference_quality import visible_mixed_html_text, _mixed_html_tag_pattern


def test_changed_markdown_tag_grammar_fails_explicitly():
    with pytest.raises(RuntimeError, match='grammar changed'):
        _mixed_html_tag_pattern(r'^</?[A-Za-z]+>')


@pytest.mark.parametrize('name', ['native:element', 'native_element', 'native.element'])
def test_namespaced_mixed_html_keeps_visibility_rules(name):
    result = visible_mixed_html_text(
        f'Prefix<sup>1</sup> x<y <{name} hidden>secret</{name}> Visible')
    assert 'secret' not in result
    assert 'x<y' in result
    assert 'Visible' in result


@pytest.mark.parametrize('comparison', ['x<y', 'a<b+c', 'k<n\n$$'])
def test_mixed_markdown_comparison_keeps_following_prose(comparison):
    source = f'Heading<sup>1</sup>\n\n{comparison}\n\nVisible sentence.\n\nTail<sub>2</sub>'
    result = visible_mixed_html_text(source)
    assert comparison in result
    assert 'Visible sentence.' in result
    assert 'Tail2' in result


@pytest.mark.parametrize('hidden', [
    '<script>secret</script>', '<style>secret</style>',
    '<span hidden>secret</span>', '<span style="display:none">secret</span>',
    '<!-- secret -->',
    '<div hidden class="a"id="b">secret</div>',
    '<script/x>secret</script>', '<style/x>secret</style>',
    '<span style="display:none"id="b">secret</span>',
    "<script x=a'b>secret</script>",
    "<div hidden x=a'b>secret</div>",
    "<span hidden x=a'b title=\"x<y\">secret</span>",
])
def test_comparison_does_not_expose_hidden_html(hidden):
    result = visible_mixed_html_text(f'Prefix<sup>1</sup>\nx<y\n\n{hidden}\nVisible sentence.')
    assert 'secret' not in result
    assert 'Visible sentence.' in result


def test_tolerated_visible_tag_keeps_its_text():
    assert 'Visible sentence.' in visible_mixed_html_text(
        '<div class="a"id="b">Visible sentence.</div> after')


def test_quoted_angle_in_valid_tag_remains_attribute():
    result = visible_mixed_html_text('Prefix<sup>1</sup><span title="x<y">Visible</span>')
    assert 'Visible' in result
    assert 'x<y' not in result


@pytest.mark.parametrize('literal', [
    'if x<y then keep this prose and z>w end', 'List<String>', 'Vec<u8>',
])
@pytest.mark.parametrize('representation', ['inline', 'fenced', 'escaped'])
def test_explicit_technical_literals_survive_the_public_scorer(literal, representation):
    from lexbench_webfetch.evaluation import evaluate_response
    if representation == 'inline':
        answer = f'`{literal}`'
    elif representation == 'fenced':
        answer = f'```text\n{literal}\n```'
    else:
        answer = literal.replace('<', '&lt;').replace('>', '&gt;')
    case = {'id': 'technical-literal', 'name': 'Technical literal',
            'url': 'https://example.com/technical', 'category': 'web',
            'expected_any': [literal]}
    assert evaluate_response(case, answer.encode())['passed']
    assert not evaluate_response(case, b'Unrelated visible answer')['passed']


def test_unknown_html_element_cannot_supply_hidden_facts():
    from lexbench_webfetch.evaluation import evaluate_response
    case = {'id': 'hidden-custom-element', 'name': 'Hidden custom element',
            'url': 'https://example.com/technical', 'category': 'web',
            'expected_any': ['secret fact']}
    assert evaluate_response(case, b'<custom>secret fact</custom>')['passed']
    assert not evaluate_response(case, b'x<custom hidden>secret fact</custom>')['passed']
