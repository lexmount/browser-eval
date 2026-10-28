"""Adjacent native links retain their own labels, subject to content scope."""
import pytest
from lexbench_webfetch.stable_tasks import evaluate_stable_task

@pytest.mark.parametrize('html,extra,passed', [
    ('<a href="/a">Resolvers</a><a href="/b">schema-typed</a>', {}, True),
    ('<a href="/a">Resolve<span>rs</span></a><a href="/b">schema-typed</a>', {}, True),
    ('<p>Resolversschema-typed</p>', {}, False),
    ('<a href="/a">someresolvers</a>', {}, False),
    ('<a hidden href="/a">Resolvers</a>', {}, False),
    ('<a href="/a"><img alt="Resolvers"></a>', {}, False),
    ('<a href="/a"><svg><title>Resolvers</title></svg></a>', {}, False),
    ('<nav><a href="/a">Resolvers</a></nav>', {'content_scope': 'target_content'}, False),
    ('<a href="/a">Resolvers</a>', {'text_exclusions': {'html_selectors': ['a']}}, False),
    ('<a href="/a">Resolvers</a>', {'text_exclusions': {'text_patterns': ['Resolvers']}}, False),
])
def test_native_link_labels(html, extra, passed):
    contract = {'version': 1, 'schema': 'body_text', 'minimum_records': 0,
                'stable_text_anchors': [{'id': 'label', 'alternatives': ['Resolvers']}], **extra}
    assert evaluate_stable_task('<html><body>' + html + '</body></html>', contract)['stable_task_pass'] is passed


@pytest.mark.parametrize('text,passed', [
    ('OpenAI Python API library', True),
    ('More itemsOpenAI Python API library', True),
    ('More items Open AI Python API library', True),
    ('More itemsopenai Python API library', False),
    ('OpenAI Python API libraryextra', False),
    ('OpenAI Python library', False),
    ('OpenAI Python API libraries', False),
])
def test_phrase_case_boundaries_normalize_both_sides(text, passed):
    from lexbench_webfetch.metrics import evaluate_content
    measured = evaluate_content(text, [], required_facts=[
        {'id': 'title', 'alternatives': ['OpenAI Python API library']}])
    assert measured['fact_results'][0]['pass'] is passed


@pytest.mark.parametrize('heading,passed', [
    ('## Object spec and statushttps://example.test/page/#object-spec', True),
    ('## Object spec and status https://example.test/page/#object-spec', True),
    ('## Object spec and statuswronghttps://example.test/page/#object-spec', False),
    ('## Wrong titlehttps://example.test/page/#object-spec-and-status', False),
    ('## Object spec and statushttps://example.test/page/', False),
])
def test_heading_permalink_preserves_exact_required_label(heading, passed):
    from lexbench_webfetch.metrics import evaluate_content
    from lexbench_webfetch.document_structure import evaluate_document_structure
    phrase = 'Object spec and status'
    text = heading + '\nA substantive explanation of the object fields.'
    measured = evaluate_content(text, [], required_facts=[
        {'id': 'heading', 'alternatives': [phrase]}])
    assert measured['fact_results'][0]['pass'] is passed
    contract = {'version': 1, 'minimum_heading_coverage': 1,
                'minimum_body_chars': 10, 'headings': [{'text': phrase, 'level': 2}]}
    assert evaluate_document_structure(text, contract)['document_structure_pass'] is passed
    assert not evaluate_document_structure(heading, contract)['document_structure_pass']
