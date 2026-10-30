"""Bare resource URIs follow comment ownership without hiding literal text."""
import copy
import html

import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt


TARGET = 'https://example.test/target.png'
NOISE = 'https://example.test/noise.png'


def resource_task(image_only=False):
    case = {'id': 'bare-comment-resource', 'name': 'Visible resource answer',
            'url': 'https://example.test/catalog', 'task_kind': 'resource',
            'content_score_applicable': False, 'expected_resources': [{'urls': [TARGET]}]}
    if image_only:
        case['resource_output_contract'] = {'candidate_scope': 'image_answer_fields', 'unexpected': 'reject'}
    return case


def score(case, source, representation=None):
    metadata = {'answer_format': representation} if representation else {}
    metrics = score_attempt(case, source.encode(), ok=True, metadata=metadata)
    return task_passed(True, metrics, 'resource'), metrics


@pytest.mark.parametrize('image_only', [False, True])
@pytest.mark.parametrize('representation', [None, 'markdown', 'html'])
@pytest.mark.parametrize('source', [f'<!-- {TARGET} -->', f'<!--\n{TARGET}\n-->',
                                     f'<!-- unclosed\n{TARGET}'])
def test_nonvisible_comment_uris_cannot_supply_a_resource(image_only, representation, source):
    passed, metrics = score(resource_task(image_only), source, representation)
    assert not passed
    assert metrics['resource_candidate_urls'] == []


@pytest.mark.parametrize('image_only', [False, True])
@pytest.mark.parametrize('representation', [None, 'markdown'])
@pytest.mark.parametrize('hidden', [f'<!-- {NOISE} -->', f'<!-- ` [hidden]({NOISE}) -->',
    f'<!-- ```\n{NOISE}\n-->'])
def test_hidden_noise_cannot_pollute_a_visible_correct_answer(image_only, representation, hidden):
    source = f'![Target]({TARGET})\n{hidden}'
    passed, metrics = score(resource_task(image_only), source, representation)
    assert passed
    assert metrics['resource_candidate_urls'] == [TARGET]
    assert metrics['resource_precision'] == 1


@pytest.mark.parametrize('image_only', [False, True])
@pytest.mark.parametrize('source', [
    rf'\<!-- {TARGET} -->', rf'\\\<!-- {TARGET} -->',
    f'&lt;!-- {TARGET} --&gt;',
    rf'\<!-- <span>{TARGET}</span> -->',
    f'`<!--` {TARGET}', f'```\n<!--\n```\n{TARGET}',
    f'    <!--\n    {NOISE}\n\n{TARGET}',
    f'<!-- ```\n{NOISE}\n-->\n{TARGET}',
])
def test_literal_comment_openers_do_not_hide_visible_uri_answers(image_only, source):
    passed, metrics = score(resource_task(image_only), source, 'markdown')
    assert passed
    assert metrics['resource_candidate_urls'] == [TARGET]


@pytest.mark.parametrize('image_only', [False, True])
def test_even_backslashes_leave_a_real_comment_opener(image_only):
    passed, metrics = score(resource_task(image_only), rf'\\<!-- {TARGET} -->', 'markdown')
    assert not passed
    assert metrics['resource_candidate_urls'] == []


@pytest.mark.parametrize('image_only', [False, True])
def test_plain_text_comment_spelling_has_no_comment_semantics(image_only):
    passed, metrics = score(resource_task(image_only), f'<!--\n{TARGET}\n-->', 'plain_text')
    assert passed
    assert metrics['resource_candidate_urls'] == [TARGET]


@pytest.mark.parametrize('image_only', [False, True])
def test_rendered_literal_comment_text_is_not_masked_a_second_time(image_only):
    source = f'<p>&lt;!--</p><p>{TARGET}</p><p>--&gt;</p>'
    passed, metrics = score(resource_task(image_only), source, 'html')
    assert passed
    assert metrics['resource_candidate_urls'] == [TARGET]
    passed, metrics = score(resource_task(image_only), f'`<!-- {TARGET} -->`', 'html')
    assert not passed
    assert metrics['resource_candidate_urls'] == []


def test_comment_mask_also_covers_bare_relative_download_filenames():
    case = resource_task()
    case['expected_resources'] = [{'urls': ['/manual.pdf']}]
    assert not score(case, '<!-- <manual.pdf> -->', 'markdown')[0]
    assert score(case, '<manual.pdf>', 'markdown')[0]


def public_controls():
    rows = []
    examples = [
        ('hidden-target', f'<!-- {TARGET} -->', None, False),
        ('hidden-noise', f'![Target]({TARGET})\n<!-- {NOISE} -->', None, True),
        ('escaped-opener', rf'\<!-- {TARGET} -->', 'markdown', True),
        ('code-owned-opener', f'`<!--` {TARGET}', 'markdown', True),
        ('native-code-comment', f'`<!-- {TARGET} -->`', 'html', False),
        ('native-eof-comment', f'<!-- unclosed\n{TARGET}', 'html', False),
        ('quoted-attribute', f'<span title="<!-- {NOISE}">{TARGET}</span>', 'markdown', True),
        ('native-literal-comment', f'<p>&lt;!--</p><p>{TARGET}</p><p>--&gt;</p>', 'html', True),
    ]
    for image_only in (False, True):
        for suffix, source, representation, expected in examples:
            case = resource_task(image_only)
            passed, metrics = score(case, source, representation)
            assert passed is expected
            identifier = 'image' if image_only else 'generic'
            row = {'id': f'review29-bare-comment-{identifier}-{suffix}',
                   'root_cause': '4408232590', 'category': 'answer-surface',
                   'failure_mode': 'false-negative' if expected else 'false-positive',
                   'case': copy.deepcopy(case), 'artifact': source, 'ok': True,
                   'expected_task_pass': expected,
                   'expected_metrics': {'resource_candidate_urls': [TARGET] if expected else []}}
            if representation:
                row['metadata'] = {'answer_format': representation}
            rows.append(row)
    for suffix, source, expected in [('hidden-filename', '<!-- <manual.pdf> -->', False),
                                     ('visible-filename', '<manual.pdf>', True)]:
        case = resource_task()
        case['expected_resources'] = [{'urls': ['/manual.pdf']}]
        passed, metrics = score(case, source, 'markdown')
        assert passed is expected
        rows.append({'id': f'review29-bare-comment-generic-{suffix}',
                     'root_cause': '4408232590', 'category': 'answer-surface',
                     'failure_mode': 'false-negative' if expected else 'false-positive',
                     'case': case, 'artifact': source, 'metadata': {'answer_format': 'markdown'},
                     'ok': True, 'expected_task_pass': expected,
                     'expected_metrics': {'resource_candidate_urls': ['https://example.test/manual.pdf'] if expected else []}})
    return rows


@pytest.mark.parametrize('image_only', [False, True])
@pytest.mark.parametrize('representation', ['markdown', 'html'])
def test_comment_spelling_in_quoted_html_attributes_cannot_hide_visible_uris(image_only, representation):
    source = f'<span title="<!-- {NOISE}">{TARGET}</span>'
    passed, metrics = score(resource_task(image_only), source, representation)
    assert passed
    assert metrics['resource_candidate_urls'] == [TARGET]


@pytest.mark.parametrize('image_only', [False, True])
@pytest.mark.parametrize('literal', [False, True])
def test_native_frame_comment_ownership_applies_before_bare_uri_discovery(image_only, literal):
    document = f'<pre>&lt;!--\n{TARGET}\n--&gt;</pre>' if literal else f'<pre><!--\n{TARGET}'
    source = '<iframe srcdoc="' + html.escape(document, quote=True) + '"></iframe>'
    passed, metrics = score(resource_task(image_only), source, 'html')
    assert passed is literal
    assert metrics['resource_candidate_urls'] == ([TARGET] if literal else [])


@pytest.mark.parametrize('image_only', [False, True])
@pytest.mark.parametrize('prefix', ['`<span title="` ', '`a longer literal prefix` '])
def test_masking_code_does_not_turn_a_real_comment_into_indented_literal_text(image_only, prefix):
    source = prefix + f'<!-- {TARGET} -->'
    passed, metrics = score(resource_task(image_only), source, 'markdown')
    assert not passed
    assert metrics['resource_candidate_urls'] == []
