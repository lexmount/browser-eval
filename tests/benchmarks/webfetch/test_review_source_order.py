"""Visibility pruning preserves native syntax and the returned record order."""
import pytest

from lexbench_webfetch.dynamic_lists import extract_named_entries
from lexbench_webfetch.evaluation import evaluate_response
from tests.benchmarks.webfetch.test_review_record_formats import record_task, task


SCOPE = {'html_selector': '.items', 'start_pattern': r'(?m)^# Items$', 'end_pattern': r'(?m)^# End$'}


@pytest.mark.parametrize('hidden', [
    '<div hidden><a href="/item/1">Widget</a></div>',
    '<span hidden>\n[Widget](/item/1)\n</span>',
    '<details><summary>More</summary>\n[Widget](/item/1)\n</details>',
    '<!--\n[Widget](/item/1)\n-->',
])
def test_text_scope_fallback_obeys_visibility(hidden):
    contract = record_task(entry_scope=SCOPE)
    assert not evaluate_response(contract, ('# Items\n' + hidden + '\n# End').encode())['passed']
    assert evaluate_response(contract, b'# Items\n[Widget](/item/1)\n# End')['passed']


def test_pruning_keeps_surviving_caption_markup_bytes_and_positions():
    scope = {**SCOPE, 'markdown_label_pattern': r'(?s)<br>\s*(?P<label>[^<>\n]+?)\s*<br>\s*$'}
    contract = record_task(entry_scope=scope)
    answer = ('# Items\n<div hidden>[Wrong](/item/2)</div>\n'
              '[Artwork<br>Widget<br>](/item/1)\n# End')
    result = evaluate_response(contract, answer.encode())
    assert result['passed']
    assert result['metrics']['stable_records'][0]['fields']['title'] == 'Widget'


@pytest.mark.parametrize('literal', ['`<div hidden><a href="/item/1">Widget</a></div>`',
                                   '```html\n<div><a href="/item/1">Widget</a></div>\n```'])
def test_literal_html_cannot_create_records_during_visibility_projection(literal):
    assert not evaluate_response(record_task(), literal.encode())['passed']


def verification_task(scope=None, minimum=3):
    rules = {'mode': 'dynamic_list_structure', 'verification_scope': 'first_three_name_detail_pairs',
             'hosts': ['shop.example'], 'entry_path': r'/item/\d+', 'minimum_entries': minimum}
    if scope:
        rules['entry_scope'] = scope
    reference = {'items': [{'url': f'https://shop.example/item/{index}', 'title': f'Confirmed {index}'}
                           for index in range(1, 5)]}
    return task(source_benchmark=rules), reference


def link(index, title, kind):
    return f'<a href="/item/{index}">{title}</a>' if kind == 'html' else f'[{title}](/item/{index})'


@pytest.mark.parametrize('leading', ['html', 'markdown'])
@pytest.mark.parametrize('wrong_first', [False, True])
def test_first_three_verification_follows_source_order_in_both_format_directions(leading, wrong_first):
    contract, reference = verification_task()
    trailing = 'markdown' if leading == 'html' else 'html'
    records = [link(index, 'Wrong' if index == (1 if wrong_first else 4) else f'Confirmed {index}',
                    leading if index <= (1 if wrong_first else 3) else trailing) for index in range(1, 5)]
    result = evaluate_response(contract, '\n'.join(records).encode(), list_reference=reference)
    assert result['passed'] is not wrong_first
    assert [row['url'] for row in result['metrics']['list_checks']] == [
        f'https://shop.example/item/{index}' for index in (1, 2, 3)]


def test_image_heading_record_keeps_its_original_position_before_html_records():
    contract, reference = verification_task()
    answer = '[![Wrong](image.jpg)](/item/1)\n\n## Wrong\n\n' + '\n'.join(
        link(index, f'Confirmed {index}', 'html') for index in (2, 3, 4))
    result = evaluate_response(contract, answer.encode(), list_reference=reference)
    assert not result['passed']
    assert result['metrics']['list_checks'][0]['url'] == 'https://shop.example/item/1'


def test_table_parser_does_not_move_a_later_record_before_html_results():
    scope = {'html_selector': '.row', 'markdown_title_column': 'Name', 'markdown_link_column': 'Details'}
    contract, reference = verification_task(scope)
    answer = '\n'.join(link(index, f'Confirmed {index}', 'html') for index in (1, 2, 3))
    answer += '\n\n| Name | Details |\n| --- | --- |\n| Wrong | [Details](/item/4) |'
    assert evaluate_response(contract, answer.encode(), list_reference=reference)['passed']


def test_hidden_pruning_does_not_change_the_first_visible_record():
    contract, reference = verification_task()
    answer = '<div hidden><a href="/item/4">Confirmed 4</a></div>\n[Wrong](/item/1)\n' + '\n'.join(
        link(index, f'Confirmed {index}', 'html') for index in (2, 3, 4))
    result = evaluate_response(contract, answer.encode(), list_reference=reference)
    assert not result['passed']
    assert result['metrics']['list_checks'][0]['url'] == 'https://shop.example/item/1'


def test_incidental_html_note_does_not_disable_markdown_product_scope():
    scope = {**SCOPE, 'html_selector': '.product', 'price_selector': '.price'}
    contract = record_task('linked_product', entry_scope=scope, price_patterns=[r'\$\d+\.\d{2}'])
    assert evaluate_response(contract, b'# Items\n[Widget](/item/1) $10.00\n<p>Note</p>\n# End')['passed']
    assert not evaluate_response(contract, b'# Items\n[Widget](/item/1) No price\n<p>Note</p>\n# End')['passed']


def test_optional_identity_capture_missing_is_an_invalid_candidate_not_a_crash():
    contract = {'hosts': ['shop.example'], 'entry_path': r'/item/(?:foo)?bar',
                'entry_identity_pattern': r'/item/(foo)?bar', 'minimum_entries': 1}
    entries = extract_named_entries('[Missing](/item/bar)\n[Valid](/item/foobar)', contract,
                                    'https://shop.example/')
    assert entries == [{'url': 'https://shop.example/item/foobar', 'title': 'Valid'}]


def test_unsupported_json_depth_does_not_leak_diagnostic_urls_as_bare_answers():
    contract = task(task_kind='resource', content_score_applicable=False,
                    expected_resources=[{'urls': ['https://shop.example/target.png']}])
    deep = '{"diagnostics":' * 1200 + '{"url":"https://shop.example/target.png"}' + '}' * 1200
    result = evaluate_response(contract, deep.encode())
    assert not result['passed']
    assert result['metrics']['resource_candidate_urls'] == []
    assert evaluate_response(contract, b'{"resources":["https://shop.example/target.png"]}')['passed']
