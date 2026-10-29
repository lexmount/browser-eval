"""Code redirects stay literal while actual blockquote containers lose their prefix."""
import re

import pytest

from lexbench_webfetch.evaluation import evaluate_response


def case(expected):
    return {'url': 'https://example.test/', 'required_patterns': [
        {'id': 'literal-code', 'match_scope': 'code_compact', 'pattern': '^' + re.escape(expected) + '$'}]}


def fence(payload, depth=0, delimiter='```'):
    prefix = '> ' * depth
    return prefix + delimiter + 'shell\n' + ''.join(prefix + line + '\n' for line in payload.splitlines()) + prefix + delimiter


def cases():
    for depth in [0, 1, 2]:
        for payload in ['> output.txt', '>> output.txt', '> > output.txt', 'if (a > b)\n> output.txt']:
            expected = re.sub(r'\s+', '', payload)
            yield f'fence-{depth}-' + payload, fence(payload, depth), expected, 'markdown', True
            yield f'fence-{depth}-lost-' + payload, fence(payload, depth), expected.replace('>', ''), 'markdown', False
    for identifier, source, expected, representation, passed in [
        ('actual-quote', '> output.txt', 'output.txt', 'markdown', True),
        ('actual-quote-not-code', '> output.txt', '>output.txt', 'markdown', False),
        ('indented-code', '    > output.txt', '>output.txt', 'markdown', True),
        ('indented-code-not-quote', '    > output.txt', 'output.txt', 'markdown', False),
        ('nested-literal-fence', fence('```literal\n> output.txt\n```', delimiter='````'), '```literal>output.txt```', 'markdown', True),
        ('quoted-unclosed', '> ```shell\n> > output.txt', '>output.txt', 'markdown', True),
        ('quoted-html-literal', fence('> x = "<b>value</b>"', 1), '>x="<b>value</b>"', 'markdown', True),
        ('quoted-html-not-projection', fence('> x = "<b>value</b>"', 1), '>x="value"', 'markdown', False),
        ('html-code', '<pre>&gt; output.txt</pre>', '>output.txt', 'html', True),
        ('plain-literal', '> output.txt', '>output.txt', 'plain_text', True),
        ('plain-not-quote', '> output.txt', 'output.txt', 'plain_text', False),
        ('numbered-listing', '```shell\n01 x = 1\n02 > output.txt\n```', 'x=1>output.txt', 'markdown', True),
    ]:
        yield identifier, source, expected, representation, passed


@pytest.mark.parametrize('identifier,source,expected,representation,passed', list(cases()))
def test_code_compaction_preserves_owned_greater_than_characters(identifier, source, expected, representation, passed):
    result = evaluate_response(case(expected), source.encode(), metadata={'answer_format': representation})
    assert result['measured']
    assert result['passed'] is passed


def scoring_controls():
    for identifier, source, expected, representation, passed in cases():
        if identifier in {'actual-quote', 'actual-quote-not-code', 'indented-code', 'indented-code-not-quote',
                          'quoted-html-literal', 'quoted-html-not-projection'} or identifier in {
                              'fence-0-> output.txt', 'fence-0-lost-> output.txt',
                              'fence-2->> output.txt', 'fence-2-lost->> output.txt'}:
            yield {'id': 'code24-' + identifier, 'category': 'answer-surface',
                   'failure_mode': 'false-negative' if passed else 'false-positive',
                   'root_cause': 'Only a fenced block’s actual quote-container prefixes are removed; redirects and greater-than operators inside its payload remain literal.',
                   'case': case(expected), 'artifact': source, 'metadata': {'answer_format': representation},
                   'ok': True, 'expected_task_pass': passed}


def boundary_cases():
    for depth in [1, 2]:
        prefix = '> ' * depth
        shortened = '> ' * (depth - 1)
        for boundary, joined in [(shortened, False), (prefix.rstrip(), True)]:
            source = prefix + '```sh\n' + prefix + '> output.txt\n' + boundary + '\n' + prefix + 'tail\n' + prefix + '```'
            for expected, passed in [('>output.txt', not joined), ('>output.txttail', joined)]:
                yield f'depth-{depth}-blank-{joined}-{passed}', source, expected, passed
    for identifier, source, expected, passed in [
        ('inline-not-fence', '```sh```\n> output.txt', '>output.txt', False),
        ('quoted-inline-not-fence', '> ```sh```\n> > output.txt', '>output.txt', False),
        ('valid-fence', '```sh\n> output.txt\n```', '>output.txt', True),
        ('valid-quoted-fence', '> ```sh\n> > output.txt\n> ```', '>output.txt', True),
        ('backtick-in-backtick-info', '```sh`version\n> output.txt\n```', '>output.txt', False),
        ('backtick-in-tilde-info', '~~~sh`version\n> output.txt\n~~~', '>output.txt', True),
    ]:
        yield identifier, source, expected, passed


@pytest.mark.parametrize('identifier,source,expected,passed', list(boundary_cases()))
def test_fence_delimiters_and_quote_container_boundaries(identifier, source, expected, passed):
    result = evaluate_response(case(expected), source.encode(), metadata={'answer_format': 'markdown'})
    assert result['measured']
    assert result['passed'] is passed


def boundary_controls():
    for identifier, source, expected, passed in boundary_cases():
        if identifier.startswith('depth-2') or identifier in {'quoted-inline-not-fence', 'valid-quoted-fence'}:
            continue
        yield {'id': 'code26-' + identifier, 'category': 'answer-surface',
               'failure_mode': 'false-negative' if passed else 'false-positive',
               'root_cause': ('A quoted fence ends when its quote container ends, including an unprefixed blank; '
                              'a backtick fence opener cannot contain backticks in its info string.'),
               'case': case(expected), 'artifact': source, 'metadata': {'answer_format': 'markdown'},
               'ok': True, 'expected_task_pass': passed, 'expected_task_measured': True}
