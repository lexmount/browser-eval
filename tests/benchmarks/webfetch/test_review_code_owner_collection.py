"""Every actual code owner is eligible; separate owners never become one block."""
import re

import pytest

from lexbench_webfetch.metrics import task_measured, task_passed
from lexbench_webfetch.scoring import score_attempt


def case(pattern):
    return {'url': 'https://example.test/', 'required_patterns': [
        {'id': 'code', 'match_scope': 'code_compact', 'pattern': pattern}]}


def scenarios():
    wrappers = {
        'indent': lambda value: '    ' + value,
        'inline': lambda value: '`' + value + '`',
        'fence': lambda value: '```python\n' + value + '\n```',
        'quoted-fence': lambda value: '> ```python\n> ' + value + '\n> ```',
    }
    for kind in ['indent', 'inline']:
        for other in ['fence', 'quoted-fence']:
            for reverse in [False, True]:
                pieces = [wrappers[kind]('required_code()'), wrappers[other]('other_code()')]
                if reverse:
                    pieces.reverse()
                source = '\n\n'.join(pieces)
                yield f'{kind}-{other}-{reverse}', source, r'^required_code\(\)$', True
                yield f'{kind}-{other}-{reverse}-other', source, r'^other_code\(\)$', True
                joined = 'other_code()required_code()' if reverse else 'required_code()other_code()'
                yield f'{kind}-{other}-{reverse}-separate', source, '^' + re.escape(joined) + '$', False
    for identifier, source, pattern, expected in [
        ('inline-separate', '`required_code()` and `other_code()`', r'^required_code\(\)other_code\(\)$', False),
        ('same-owner', '```python\nrequired_code()\nother_code()\n```', r'^required_code\(\)other_code\(\)$', True),
        ('indent-separated-prose', '    required_code()\n\nNote\n\n    other_code()', r'^required_code\(\)other_code\(\)$', False),
        ('quoted-indent', '>     > output.txt\n\n```sh\nother_code()\n```', r'^>output\.txt$', True),
        ('comment-owned-fence', '<!--\n```python\nrequired_code()\n-->\nOther text', r'required_code\(\)', False),
        ('mixed-html-inline', 'Intro\n\n<pre>other_code()</pre>\n\n`required_code()`', r'^required_code\(\)$', True),
        ('mixed-html-separate', 'Intro\n\n<pre>other_code()</pre>\n\n`required_code()`', r'other_code\(\).*required_code\(\)', False),
        ('mixed-html-code', '<code>required_code()</code>\n\n```python\nother_code()\n```', r'^required_code\(\)$', True),
        ('mixed-html-code-separate', '<code>required_code()</code>\n\n```python\nother_code()\n```', r'required_code\(\).*other_code\(\)', False),
        ('no-code-fallback', 'required_code()', r'^required_code\(\)$', True),
    ]:
        yield identifier, source, pattern, expected


@pytest.mark.parametrize('identifier,source,pattern,expected', list(scenarios()))
def test_all_code_owners_remain_independently_eligible(identifier, source, pattern, expected):
    measured = score_attempt(case(pattern), source.encode(), ok=True, metadata={'answer_format': 'markdown'})
    assert task_measured(measured)
    assert task_passed(True, measured, 'body') is expected


def scoring_controls():
    selected = {'indent-fence-False', 'inline-fence-False', 'indent-fence-False-separate',
                'inline-fence-False-separate', 'same-owner', 'inline-separate', 'quoted-indent', 'comment-owned-fence',
                'mixed-html-code', 'mixed-html-code-separate'}
    for identifier, source, pattern, expected in scenarios():
        if identifier in selected:
            yield {'id': 'code28-' + identifier, 'category': 'answer-surface',
                   'failure_mode': 'false-negative' if expected else 'false-positive',
                   'root_cause': 'Code matching collects every actual literal owner while retaining independent owner boundaries.',
                   'case': case(pattern), 'artifact': source, 'ok': True, 'metadata': {'answer_format': 'markdown'},
                   'expected_task_pass': expected, 'expected_task_measured': True}
