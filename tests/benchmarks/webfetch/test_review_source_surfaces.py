"""HTML attributes and metadata arrays cannot fabricate document record starts."""
import pytest

from lexbench_webfetch.evaluation import evaluate_response
from tests.benchmarks.webfetch.test_review_source_order import verification_task, link
from tests.benchmarks.webfetch.test_review_record_formats import record_task


@pytest.mark.parametrize('markup', [
    '<img src="widget.png" alt="\n[Confirmed 1](/item/1)\n">',
    '<span title="\n[Confirmed 1](/item/1)\n">Visible prose</span>',
    '<a href="/help" data-label="\n[Confirmed 1](/item/1)\n">Help</a>',
    '<span title=\'[>\n[Confirmed 1](/item/1)\n\'>Visible prose</span>',
])
def test_markdown_syntax_in_any_html_attribute_is_not_a_record(markup):
    contract, reference = verification_task(minimum=1)
    result = evaluate_response(contract, (markup + '\nOrdinary visible content').encode(), list_reference=reference)
    assert not result['passed']
    assert result['metrics']['checked_entries'] == 0


def test_native_html_href_and_visible_label_remain_available():
    contract, reference = verification_task(minimum=1)
    text = '<a href="/item/1" title="\n[Wrong](/item/2)\n">Confirmed 1</a>'
    result = evaluate_response(contract, text.encode(), list_reference=reference)
    assert result['passed']
    assert result['metrics']['named_entry_count'] == 1


def test_attribute_delimiters_do_not_corrupt_a_real_markdown_caption_link():
    scope = {'html_selector': '.card', 'markdown_label_pattern': r'(?s)<br>\s*(?P<label>[^<>\n]+?)\s*<br>\s*$'}
    text = '[Artwork<span title="[orphan">Display</span><br>Confirmed 1<br>](/item/1)'
    result = evaluate_response(record_task(entry_scope=scope), text.encode())
    assert result['passed']
    assert result['metrics']['stable_records'][0]['fields']['title'] == 'Confirmed 1'


@pytest.mark.parametrize('explicit_last', [False, True])
def test_incidental_prose_cannot_promote_metadata_before_explicit_records(explicit_last):
    contract, reference = verification_task()
    text = 'Wrong appears in this introduction.\n' + '\n'.join(
        link(index, f'Confirmed {index}', 'html') for index in (1, 2, 3))
    if explicit_last:
        text += '\n' + link(4, 'Wrong', 'html')
    result = evaluate_response(contract, text.encode(), list_reference=reference,
                               metadata={'links': [{'url': '/item/4', 'title': 'Wrong'}]})
    assert result['passed']
    assert [row['url'] for row in result['metrics']['list_checks']] == [
        f'https://shop.example/item/{index}' for index in (1, 2, 3)]


def test_metadata_only_records_keep_their_declared_array_order():
    contract, reference = verification_task()
    metadata = {'links': [{'url': '/item/4', 'title': 'Wrong'},
                           {'url': '/item/1', 'title': 'Confirmed 1'},
                           {'url': '/item/2', 'title': 'Confirmed 2'},
                           {'url': '/item/3', 'title': 'Confirmed 3'}]}
    result = evaluate_response(contract, b'Confirmed 3 is mentioned first in ordinary prose.',
                               list_reference=reference, metadata=metadata)
    assert not result['passed']
    assert [row['url'] for row in result['metrics']['list_checks']] == [
        'https://shop.example/item/4', 'https://shop.example/item/1', 'https://shop.example/item/2']
