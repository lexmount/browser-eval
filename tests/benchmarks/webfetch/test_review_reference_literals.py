"""Markdown escapes, code and HTML visibility have distinct evidence surfaces."""
import pytest

from lexbench_webfetch.body_coverage import body_requirements
from lexbench_webfetch.evaluation import evaluate_response
from lexbench_webfetch.reference_quality import (
    markdown_links, visible_markdown_links, visible_mixed_html_text,
)


@pytest.mark.parametrize('slashes', range(5))
def test_only_unescaped_opening_brackets_create_links(slashes):
    value = '\\' * slashes + '[Download](/manual.pdf)'
    links = markdown_links(value)
    assert bool(links) is (slashes % 2 == 0)
    visible = visible_markdown_links(value)
    assert ('/manual.pdf' in visible) is (slashes % 2 == 1)
    task = {'url': 'https://example.test/',
            'required_facts': [{'alternatives': ['/manual.pdf']}]}
    assert evaluate_response(task, value.encode())['passed'] is (slashes % 2 == 1)


@pytest.mark.parametrize('value,image', [
    (r'\![Download](/manual.pdf)', False),
    (r'\\![Download](/manual.pdf)', True),
])
def test_escaped_image_bang_keeps_its_existing_parity(value, image):
    assert len(markdown_links(value)) == 1
    assert markdown_links(value)[0].is_image is image


@pytest.mark.parametrize('value,expected', [
    ('`\n<!-- Secret required phrase -->\n`', True),
    ('``\n<!-- Secret required phrase -->\n``', True),
    ('`\n<label hidden>Secret required phrase</label>\n`', True),
    ('| ```html\n<label hidden>Secret required phrase</label>\n```', True),
    ('| ```html\n<label hidden>Secret required phrase</label>', False),
    ('```html\n<!-- Secret required phrase -->\n```', True),
    ('Intro\n<!-- Secret required phrase -->', False),
    ('\\`\n<!-- Secret required phrase -->\n`', False),
])
@pytest.mark.parametrize('body', [False, True])
def test_multiline_literals_preserve_comments_and_hidden_tag_examples(value, expected, body):
    requirements = ({'body_requirements': body_requirements('Secret required phrase')}
                    if body else {'required_facts': [{'alternatives': ['Secret required phrase']}]})
    task = {'url': 'https://example.test/', **requirements}
    assert evaluate_response(task, value.encode())['passed'] is expected


@pytest.mark.parametrize('tag', ['label', 'button', 'x-evidence', 'svg:g'])
@pytest.mark.parametrize('attribute', ['', 'hidden', 'style="display:none"', 'style="visibility:hidden"'])
@pytest.mark.parametrize('body', [False, True])
def test_generic_html_visibility_applies_to_facts_and_body(tag, attribute, body):
    value = f'Intro\n<{tag} {attribute}>Secret required phrase</{tag}>'
    requirements = ({'body_requirements': body_requirements('Secret required phrase')}
                    if body else {'required_facts': [{'alternatives': ['Secret required phrase']}]})
    task = {'url': 'https://example.test/', **requirements}
    assert evaluate_response(task, value.encode())['passed'] is (not attribute)


@pytest.mark.parametrize('value', [
    '<https://example.test/guide>', '<ftp://example.test/guide>',
    '<mailto:reader@example.test>', '<reader@example.test>', '</docs/start>',
    '`<label hidden>Literal example</label>`',
    '`\n<label hidden>Literal example</label>\n`',
])
def test_generic_html_projection_keeps_autolinks_and_literals(value):
    assert visible_mixed_html_text(value) == value


@pytest.mark.parametrize('value', [
    '`[Download](/manual.pdf)`', '`\n[Download](/manual.pdf)\n`',
    '```markdown\n[Download](/manual.pdf)\n```',
])
def test_code_link_destinations_are_visible_literal_prose(value):
    assert visible_markdown_links(value) == value
    task = {'url': 'https://example.test/',
            'required_facts': [{'alternatives': ['/manual.pdf']}]}
    assert evaluate_response(task, value.encode())['passed']


def scoring_controls():
    def control(identifier, text, phrase, expected, *, body=False, cause):
        requirements = ({'body_requirements': body_requirements(phrase)} if body
                        else {'required_facts': [{'alternatives': [phrase]}]})
        return {'id': 'reference4-' + identifier, 'category': 'format-normalization',
                'failure_mode': 'false-negative' if expected else 'false-positive',
                'root_cause': cause, 'case': {'url': 'https://example.test/', **requirements},
                'artifact': text, 'ok': True, 'expected_task_pass': expected}
    for slashes in range(5):
        yield control(f'opening-bracket-{slashes}', '\\' * slashes + '[Download](/manual.pdf)',
                      '/manual.pdf', slashes % 2 == 1,
                      cause='Only an unescaped opening bracket establishes link syntax; escaped destinations remain visible prose.')
    for body in (False, True):
        for literal in (False, True):
            text = '<!-- Secret required phrase -->'
            if literal:
                text = '`\n' + text + '\n`'
            yield control(f'multiline-comment-{body}-{literal}', text, 'Secret required phrase', literal,
                          body=body, cause='Multiline code spans must be protected before nonliteral HTML comments are removed.')
        for tag in ('label', 'x-evidence'):
            for hidden in (False, True):
                text = f'Intro\n<{tag}' + (' hidden' if hidden else '') + f'>Secret required phrase</{tag}>'
                yield control(f'generic-tag-{body}-{tag}-{hidden}', text, 'Secret required phrase', not hidden,
                              body=body, cause='Visibility filtering must apply to generic HTML elements, not a tag-name whitelist.')
        for complete in (False, True):
            text = '| ```html\n<label hidden>Secret required phrase</label>' + ('\n```' if complete else '')
            yield control(f'native-cell-fence-{body}-{complete}', text, 'Secret required phrase', complete,
                          body=body, cause='Only complete native table-cell fence spelling supplies literal markup content.')
    yield control('code-destination', '`[Download](/manual.pdf)`', '/manual.pdf', True,
                  cause='A code example displays its complete link spelling, including its destination.')
