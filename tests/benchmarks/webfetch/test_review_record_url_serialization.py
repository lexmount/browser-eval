"""A syntax reader decodes once; record identity retains the resulting URL."""
import pytest

from lexbench_webfetch.dynamic_lists import visible_html_text
from lexbench_webfetch.evaluation import evaluate_response
from lexbench_webfetch.reference_quality import markdown_links
from tests.benchmarks.webfetch.test_review_record_formats import record_task


@pytest.mark.parametrize('source,artifact,metadata', [
    ('markdown', '[Alpha](/item&amp;amp;/detail)', None),
    ('markdown-escaped', r'[Alpha](/item\&amp;/detail)', None),
    ('html', '<a href="/item&amp;amp;/detail">Alpha</a>', None),
    ('metadata', 'Visible introduction', {'links': [{'url': '/item&amp;/detail', 'title': 'Alpha'}]}),
])
@pytest.mark.parametrize('suffix', ['', '/detail'])
def test_entity_spelling_inside_an_actual_url_is_not_decoded_again(source, artifact, metadata, suffix):
    artifact = artifact.replace('/detail', suffix)
    if metadata:
        metadata = {'links': [{'url': '/item&amp;' + suffix, 'title': 'Alpha'}]}
    assert evaluate_response(record_task(entry_path=r'/item&amp;' + suffix), artifact.encode(), metadata=metadata)['passed']
    assert not evaluate_response(record_task(entry_path=r'/item&' + suffix), artifact.encode(), metadata=metadata)['passed']


@pytest.mark.parametrize('url', ['/item&', '/item%26', '/item%2Fchild'])
@pytest.mark.parametrize('source', ['markdown', 'html', 'metadata'])
def test_plain_ampersands_and_reserved_url_octets_keep_their_identity(url, source):
    metadata = {'links': [{'url': url, 'title': 'Alpha'}]} if source == 'metadata' else None
    artifact = f'[Alpha]({url})' if source == 'markdown' else f'<a href="{url}">Alpha</a>'
    if metadata:
        artifact = 'Visible introduction'
    assert evaluate_response(record_task(entry_path=url), artifact.encode(), metadata=metadata)['passed']


def test_html_to_markdown_serialization_does_not_redecode_a_url_entity():
    source = '<a href="/item&amp;amp;/detail">Alpha</a>'
    rendered = visible_html_text(source, preserve_links=True)
    assert markdown_links(rendered)[0].destination == '/item&amp;/detail'
    assert evaluate_response(record_task(entry_path=r'/item&amp;/detail'), rendered.encode())['passed']


def test_product_html_reader_decodes_its_attribute_once_in_text_scope():
    scope = {'html_selector': '.items', 'start_pattern': r'^# Items$', 'end_pattern': r'^# End$'}
    source = '# Items\n<a href="/item&amp;amp;/detail">Alpha</a> $10\n# End'
    case = record_task('linked_product', entry_path=r'/item&amp;/detail', price_patterns=[r'\$\d+'], entry_scope=scope)
    assert evaluate_response(case, source.encode())['passed']
    case['acceptance_contract']['entry_path'] = r'/item&/detail'
    assert not evaluate_response(case, source.encode())['passed']
