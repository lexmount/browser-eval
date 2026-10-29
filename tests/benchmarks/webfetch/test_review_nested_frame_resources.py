"""Inline HTML frame resources share visible, bounded document traversal."""
import html

import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt


ROOT = 'https://example.test/catalog/index.html'


def frame(body, *, url=None, attributes=''):
    declared = ' data-frame-url="' + html.escape(url, quote=True) + '"' if url else ''
    return '<iframe' + declared + ' ' + attributes + ' srcdoc="' + html.escape(body, quote=True) + '"></iframe>'


def task(target, image_only=False):
    result = {'id': 'nested-frame-resource', 'url': ROOT, 'name': 'Nested frame resource',
              'task_kind': 'resource', 'content_score_applicable': False,
              'expected_resources': [{'urls': [target]}]}
    if image_only:
        result['resource_output_contract'] = {'candidate_scope': 'image_answer_fields', 'unexpected': 'reject'}
    return result


def score(source, target, image_only=False, representation='html'):
    metrics = score_attempt(task(target, image_only), source.encode(), ok=True,
                            metadata={'answer_format': representation})
    return task_passed(True, metrics, 'resource'), metrics


def link(path, image_only=False):
    return '<img src="' + html.escape(path, quote=True) + '">' if image_only else '<a href="' + html.escape(path, quote=True) + '">Download</a>'


@pytest.mark.parametrize('image_only', [False, True])
@pytest.mark.parametrize('depth', [1, 2, 5, 20])
def test_visible_nested_frame_resources_are_delivered(image_only, depth):
    source = link('/target.png' if image_only else '/target.pdf', image_only)
    target = 'https://example.test/target.' + ('png' if image_only else 'pdf')
    for _ in range(depth):
        source = frame(source)
    assert score(source, target, image_only)[0]


@pytest.mark.parametrize('image_only', [False, True])
@pytest.mark.parametrize('hidden', ['hidden', 'style="display:none"', 'width="0" height="0"'])
def test_hidden_nested_document_cannot_deliver_resource(image_only, hidden):
    path = '/target.png' if image_only else '/target.pdf'
    source = frame(frame(link(path, image_only), attributes=hidden))
    assert not score(source, 'https://example.test' + path, image_only)[0]
    assert score(frame(frame(link(path, image_only))), 'https://example.test' + path, image_only)[0]


@pytest.mark.parametrize('image_only', [False, True])
@pytest.mark.parametrize('inner_url,path,target', [
    (None, 'target.png', 'https://frames.example/outer/target.png'),
    ('https://inner.example/docs/page', 'target.png', 'https://inner.example/docs/target.png'),
    ('https://inner.example/docs/page', '/target.png', 'https://inner.example/target.png'),
    ('https://[malformed', 'target.png', 'https://frames.example/outer/target.png'),
])
def test_each_frame_uses_its_declared_or_inherited_document_base(image_only, inner_url, path, target):
    source = frame(frame(link(path, image_only), url=inner_url), url='https://frames.example/outer/page')
    assert score(source, target, image_only)[0]
    assert not score(source, 'https://wrong.example/target.png', image_only)[0]


@pytest.mark.parametrize('image_only', [False, True])
def test_document_base_href_is_inherited_by_nested_srcdoc(image_only):
    source = frame('<base href="https://assets.example/files/">' + frame(link('target.png', image_only)))
    assert score(source, 'https://assets.example/files/target.png', image_only)[0]
    assert not score(source, 'https://example.test/catalog/target.png', image_only)[0]


@pytest.mark.parametrize('image_only', [False, True])
def test_nested_attribute_entities_are_decoded_once(image_only):
    target = 'https://example.test/target.png?a=1&amp;b=2'
    source = frame(frame(link(target, image_only)))
    assert score(source, target, image_only)[0]
    assert not score(source, target.replace('&amp;', '&'), image_only)[0]


@pytest.mark.parametrize('image_only', [False, True])
@pytest.mark.parametrize('body', ['<!-- <img src="/target.png"> -->',
    '<span title="&lt;img src=/target.png&gt;">No resource</span>',
    '<pre>&lt;img src="/target.png"&gt;</pre>'])
def test_nested_nonresource_syntax_does_not_deliver_an_image(body, image_only):
    assert not score(frame(frame(body)), 'https://example.test/target.png', image_only)[0]


@pytest.mark.parametrize('image_only', [False, True])
def test_markdown_frame_examples_are_not_live_documents(image_only):
    source = frame(frame(link('/target.png', image_only)))
    assert score(source, 'https://example.test/target.png', image_only, 'markdown')[0]
    assert not score('```html\n' + source + '\n```', 'https://example.test/target.png', image_only, 'markdown')[0]


@pytest.mark.parametrize('image_only', [False, True])
@pytest.mark.parametrize('count,expected', [(100, True), (101, False)])
def test_frame_budget_is_global_and_truncation_is_an_explicit_failure(image_only, count, expected):
    source = frame(link('/target.png', image_only)) + frame('<p>No resource</p>') * (count - 1)
    passed, metrics = score(source, 'https://example.test/target.png', image_only)
    assert passed is expected
    if not expected:
        assert '100' in metrics['resource_error']
        assert not metrics['resource_pass']


@pytest.mark.parametrize('image_only', [False, True])
def test_relative_base_href_is_applied_once_per_document(image_only):
    source = frame('<base href="assets/">' + frame(frame(link('target.png', image_only))))
    assert score(source, 'https://example.test/catalog/assets/target.png', image_only)[0]
    assert not score(source, 'https://example.test/catalog/assets/assets/target.png', image_only)[0]


@pytest.mark.parametrize('image_only', [False, True])
def test_hidden_noise_frames_do_not_use_the_visible_frame_budget(image_only):
    source = frame(link('/target.png', image_only)) + frame('<p>No resource</p>', attributes='hidden') * 101
    passed, metrics = score(source, 'https://example.test/target.png', image_only)
    assert passed
    assert 'resource_error' not in metrics


@pytest.mark.parametrize('image_only', [False, True])
def test_hidden_nested_noise_does_not_pollute_a_correct_answer(image_only):
    source = frame(link('/target.png', image_only) + '<div hidden>' + frame(link('/noise.png', image_only)) + '</div>')
    passed, metrics = score(source, 'https://example.test/target.png', image_only)
    assert passed
    assert metrics['resource_candidate_urls'] == ['https://example.test/target.png']
