"""HTML resource URLs use document bases without rebasing independent metadata."""
import html
import json

import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt


RESPONSE = 'https://example.test/catalog/index.html'
ORIGINAL = 'https://example.test/catalog/target.png'
ASSET = 'https://assets.example/files/target.png'


def task(target, image_only=False):
    case = {'id': 'document-base-resource', 'url': RESPONSE, 'task_kind': 'resource',
            'content_score_applicable': False, 'expected_resources': [{'urls': [target]}]}
    if image_only:
        case['resource_output_contract'] = {'candidate_scope': 'image_answer_fields', 'unexpected': 'reject'}
    return case


def score(source, target, image_only=False, representation='html', metadata=None):
    attributes = dict(metadata or {})
    if representation:
        attributes['answer_format'] = representation
    metrics = score_attempt(task(target, image_only), source.encode(), ok=True, metadata=attributes)
    return task_passed(True, metrics, 'resource'), metrics


@pytest.mark.parametrize('image_only', [False, True])
@pytest.mark.parametrize('representation', ['html', None])
@pytest.mark.parametrize('element', ['<img src="target.png">', '<a href="target.png">Download</a>',
    '<img srcset="target.png 1x, target.png 2x">', '<picture><source srcset="target.png 2x"></picture>'])
def test_top_level_html_resources_resolve_against_document_base(image_only, representation, element):
    source = '<base href="https://assets.example/files/">' + element
    assert score(source, ASSET, image_only, representation)[0]
    assert not score(source, ORIGINAL, image_only, representation)[0]


@pytest.mark.parametrize('image_only', [False, True])
@pytest.mark.parametrize('prefix,expected', [
    ('<base href="files/">', 'https://example.test/catalog/files/target.png'),
    ('<base href="https://assets.example/files/"><base href="https://later.example/">', ASSET),
    ('<base target="_blank"><base href="https://assets.example/files/">', ASSET),
    ('<base href=""><base href="https://later.example/">', ORIGINAL),
    ('<base href><base href="https://later.example/">', ORIGINAL),
    ('<base hidden href="https://assets.example/files/">', ASSET),
    ('<base style="display:none" href="https://assets.example/files/">', ASSET),
    ('<head><base href="https://assets.example/files/"></head><body>', ASSET),
])
def test_first_href_bearing_base_owns_resolution_even_when_hidden(image_only, prefix, expected):
    assert score(prefix + '<img src="target.png">', expected, image_only)[0]
    assert not score(prefix + '<img src="target.png">', 'https://later.example/target.png', image_only)[0]


@pytest.mark.parametrize('image_only', [False, True])
@pytest.mark.parametrize('prefix', [
    '<!-- <base href="https://assets.example/files/"> -->',
    '<template><base href="https://assets.example/files/"></template>',
    '<textarea><base href="https://assets.example/files/"></textarea>',
    '&lt;base href="https://assets.example/files/"&gt;',
    '<span title="&lt;base href=https://assets.example/files/&gt;">Introduction</span>',
])
def test_inert_or_literal_base_examples_do_not_change_document_base(image_only, prefix):
    source = prefix + '<img src="target.png">'
    assert score(source, ORIGINAL, image_only)[0]
    assert not score(source, ASSET, image_only)[0]


@pytest.mark.parametrize('image_only', [False, True])
def test_html_base_does_not_rebase_independent_metadata(image_only):
    source = '<base href="https://assets.example/files/"><p>Introduction</p>'
    metadata = {'images': ['target.png']} if image_only else {'links': [{'url': 'target.png'}]}
    assert score(source, ORIGINAL, image_only, metadata=metadata)[0]
    assert not score(source, ASSET, image_only, metadata=metadata)[0]


@pytest.mark.parametrize('image_only', [False, True])
def test_nested_frames_inherit_top_level_base_without_reapplying_it(image_only):
    frame = '<iframe srcdoc="' + html.escape('<img src="target.png">', quote=True) + '"></iframe>'
    source = '<base href="assets/">' + frame
    assert score(source, 'https://example.test/catalog/assets/target.png', image_only)[0]
    assert not score(source, 'https://example.test/catalog/assets/assets/target.png', image_only)[0]


@pytest.mark.parametrize('image_only', [False, True])
def test_native_html_envelopes_keep_their_own_base(image_only):
    source = json.dumps({'html': '<base href="https://assets.example/files/"><img src="target.png">'})
    assert score(source, ASSET, image_only, None)[0]
    assert not score(source, ORIGINAL, image_only, None)[0]


@pytest.mark.parametrize('image_only', [False, True])
def test_absolute_bare_uris_and_plain_text_keep_literal_answer_semantics(image_only):
    source = '<base href="https://assets.example/files/">\n' + ORIGINAL
    assert score(source, ORIGINAL, image_only)[0]
    assert not score(source, ASSET, image_only)[0]
    assert score(source, ORIGINAL, image_only, 'plain_text')[0]
    assert not score(source, ASSET, image_only, 'plain_text')[0]


@pytest.mark.parametrize('image_only', [False, True])
@pytest.mark.parametrize('href', ['javascript:example', 'data:text/plain,example'])
def test_forbidden_base_schemes_keep_response_fallback_and_ignore_later_base(image_only, href):
    source = '<base href="' + href + '"><base href="https://later.example/"><img src="target.png">'
    assert score(source, ORIGINAL, image_only)[0]
    assert not score(source, 'https://later.example/target.png', image_only)[0]


@pytest.mark.parametrize('image_only', [False, True])
def test_malformed_base_does_not_borrow_response_or_later_base(image_only):
    prefix = '<base href="https://[broken"><base href="https://later.example/">'
    assert not score(prefix + '<img src="target.png">', ORIGINAL, image_only)[0]
    assert not score(prefix + '<img src="target.png">', 'https://later.example/target.png', image_only)[0]
    assert score(prefix + '<img src="' + ORIGINAL + '">', ORIGINAL, image_only)[0]


@pytest.mark.parametrize('image_only', [False, True])
def test_mixed_html_and_metadata_resources_keep_separate_bases(image_only):
    case = task(ASSET, image_only)
    metadata_target = 'https://example.test/catalog/metadata.png'
    case['expected_resources'].append({'urls': [metadata_target]})
    metadata = {'images': ['metadata.png']} if image_only else {'links': [{'url': 'metadata.png'}]}
    metadata['answer_format'] = 'html'
    metrics = score_attempt(case, b'<base href="https://assets.example/files/"><img src="target.png">',
                            ok=True, metadata=metadata)
    assert task_passed(True, metrics, 'resource')
    assert metadata_target in metrics['resource_candidate_urls']
    assert 'https://assets.example/files/metadata.png' not in metrics['resource_candidate_urls']


@pytest.mark.parametrize('image_only', [False, True])
def test_fenced_base_examples_do_not_rebase_native_markup(image_only):
    source = '```html\n<base href="https://assets.example/files/">\n```\n<img src="target.png">'
    assert score(source, ORIGINAL, image_only, 'markdown')[0]
    assert not score(source, ASSET, image_only, 'markdown')[0]


@pytest.mark.parametrize('image_only', [False, True])
@pytest.mark.parametrize('delivery', ['<img src="target.png">', '<a href="target.png">Download</a>'])
@pytest.mark.parametrize('surface', ['top', 'frame'])
def test_base_is_context_not_delivered_resource_evidence(image_only, delivery, surface):
    source = '<base href="https://assets.example/files/target.png">'
    def view(value):
        return ('<iframe srcdoc="' + html.escape(value, quote=True) + '"></iframe>') if surface == 'frame' else value
    passed, metrics = score(view(source), ASSET, image_only)
    assert not passed
    assert metrics['resource_candidate_urls'] == []
    assert score(view(source + delivery), ASSET, image_only)[0]


RAW_TEXT_ELEMENTS = ('title', 'textarea', 'script', 'style', 'xmp', 'iframe', 'noembed', 'noframes', 'noscript')


@pytest.mark.parametrize('image_only', [False, True])
@pytest.mark.parametrize('tag', RAW_TEXT_ELEMENTS)
@pytest.mark.parametrize('representation', ['html', None])
def test_raw_text_tag_examples_do_not_choose_the_document_base(image_only, tag, representation):
    example = f'<{tag}>Example <base href="https://assets.example/files/"></{tag}>'
    source = example + '<img src="target.png">'
    assert score(source, ORIGINAL, image_only, representation)[0]
    assert not score(source, ASSET, image_only, representation)[0]
    assert score(example + '<base href="https://assets.example/files/"><img src="target.png">', ASSET, image_only, representation)[0]


@pytest.mark.parametrize('image_only', [False, True])
@pytest.mark.parametrize('tag', RAW_TEXT_ELEMENTS + ('plaintext',))
@pytest.mark.parametrize('example', ['<img src="target.png">', '<a href="target.png">Download</a>',
                                    '<picture><source srcset="target.png 2x"></picture>'])
def test_raw_text_pseudo_resources_are_not_native_attributes(image_only, tag, example):
    source = f'<{tag}>Example {example}</{tag}>'
    assert not score(source, ORIGINAL, image_only)[0]
    assert score('<head><base href="https://assets.example/files/"></head>' + example, ASSET, image_only)[0]


@pytest.mark.parametrize('image_only', [False, True])
def test_plaintext_owns_the_rest_even_after_a_literal_closing_tag(image_only):
    source = '<plaintext><base href="https://assets.example/files/"></plaintext><img src="target.png">'
    assert not score(source, ORIGINAL, image_only)[0]
    assert not score(source, ASSET, image_only)[0]
    metadata = {'images': ['target.png']} if image_only else {'links': [{'url': 'target.png'}]}
    assert score(source, ORIGINAL, image_only, metadata=metadata)[0]


@pytest.mark.parametrize('image_only', [False, True])
def test_rcdata_nested_looking_tags_do_not_change_its_closing_owner(image_only):
    source = ('<textarea><template></template><base href="https://ignored.example/"></textarea>'
              '<base href="https://assets.example/files/"><img src="target.png">')
    assert score(source, ASSET, image_only)[0]


@pytest.mark.parametrize('image_only', [False, True])
@pytest.mark.parametrize('tag', ['title', 'textarea', 'noscript', 'plaintext'])
def test_raw_text_frame_examples_cannot_create_inline_documents(image_only, tag):
    frame = '<iframe srcdoc="' + html.escape('<img src="target.png">', quote=True) + '"></iframe>'
    assert not score(f'<{tag}>{frame}</{tag}>', ORIGINAL, image_only)[0]


@pytest.mark.parametrize('image_only', [False, True])
def test_picture_ownership_uses_positions_after_supported_declaration_cleanup(image_only):
    source = '<![UNRECOGNIZED[ opaque ]]><picture><source srcset="target.png 2x"></picture>'
    assert score(source, ORIGINAL, image_only)[0]


@pytest.mark.parametrize('image_only', [False, True])
@pytest.mark.parametrize('tail', ['frame', 'picture'])
def test_plaintext_literal_terminator_cannot_activate_frame_or_picture_tail(image_only, tail):
    markup = ('<iframe srcdoc="' + html.escape('<img src="target.png">', quote=True) + '"></iframe>') if tail == 'frame' else '<picture><source srcset="target.png 2x"></picture>'
    source = '<plaintext>Example </plaintext>' + markup
    passed, metrics = score(source, ORIGINAL, image_only)
    assert not passed
    assert metrics['resource_candidate_urls'] == []


@pytest.mark.parametrize('image_only,expected', [(False, True), (True, False)])
def test_equal_srcset_value_elsewhere_cannot_validate_a_raw_text_picture(image_only, expected):
    source = ('<textarea><picture><source srcset="target.png 2x"></picture></textarea>'
              '<video><source srcset="target.png 2x"></video>')
    assert score(source, ORIGINAL, image_only)[0] is expected
