"""Incidental HTML preserves Markdown records and native literal-name semantics."""
import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt
from tests.benchmarks.webfetch.test_review_owned_fields_exclusions import composite_case


def group(fields=False, wildcard=False, minimum=1):
    result = {'id': 'items', 'kind': 'named_link', 'minimum_records': minimum,
              'hosts': ['*'] if wildcard else ['example.test'], 'entry_path': r'/item/\d+'}
    if fields:
        result['required_fields'] = [{'id': 'price', 'kind': 'price', 'aliases': ['Price']}]
    return result


@pytest.mark.parametrize('representation', [None, 'markdown'])
@pytest.mark.parametrize('fields', [False, True])
@pytest.mark.parametrize('wildcard', [False, True])
@pytest.mark.parametrize('incidental', [False, True])
def test_incidental_html_does_not_remove_markdown_record(representation, fields, wildcard, incidental):
    artifact = '- [First House](/item/1)'+('\nPrice: $12' if fields else '')
    if incidental:
        artifact += '\n\n<a href="/help">Help</a>'
    metadata = {'answer_format': representation} if representation else None
    metrics = score_attempt(composite_case(group(fields, wildcard)), artifact.encode(), ok=True, metadata=metadata)
    assert task_passed(True, metrics, 'body')


@pytest.mark.parametrize('representation', [None, 'markdown'])
@pytest.mark.parametrize('fields', [False, True])
def test_mixed_native_and_markdown_records_both_contribute(representation, fields):
    artifact = '- [First House](/item/1)'+('\nPrice: $12' if fields else '')
    artifact += '\n\n<article><a href="/item/2">**Second House**</a><p>Price: $24</p></article>'
    metadata = {'answer_format': representation} if representation else None
    metrics = score_attempt(composite_case(group(fields, minimum=2)), artifact.encode(), ok=True, metadata=metadata)
    assert task_passed(True, metrics, 'body')
    assert {row['title'] for row in metrics['composite_group_results'][0]['records']} == {'First House', '**Second House**'}


@pytest.mark.parametrize('representation', [None, 'markdown'])
def test_incidental_html_attributes_cannot_supply_markdown_fields(representation):
    artifact = '- [First House](/item/1)\n\n<a href="/help" data-price="$12">Help</a>'
    metadata = {'answer_format': representation} if representation else None
    metrics = score_attempt(composite_case(group(True)), artifact.encode(), ok=True, metadata=metadata)
    assert not task_passed(True, metrics, 'body')


@pytest.mark.parametrize('incidental', [False, True])
def test_declared_html_does_not_turn_markdown_examples_into_native_anchors(incidental):
    artifact = '- [First House](/item/1)' + ('\n<a href="/help">Help</a>' if incidental else '')
    metrics = score_attempt(composite_case(group()), artifact.encode(), ok=True, metadata={'answer_format': 'html'})
    assert not task_passed(True, metrics, 'body')


@pytest.mark.parametrize('fields', [False, True])
@pytest.mark.parametrize('wildcard', [False, True])
def test_markdown_exclusions_cannot_be_repaired_by_an_out_of_scope_native_label(fields, wildcard):
    contract = group(fields, wildcard)
    contract.update(record_name_exclusions=['Alpha'], start_pattern=r'^# Catalog$', end_pattern=r'^# End$')
    artifact = '<a href="/item/1">**Alpha**</a>\n# Catalog\n- [**Alpha**](/item/1)\nPrice: $12\n# End'
    # Leading Markdown keeps this answer mixed, rather than inferring an HTML document.
    artifact = 'Intro\n' + artifact
    metrics = score_attempt(composite_case(contract), artifact.encode(), ok=True, metadata={'answer_format': 'markdown'})
    assert not task_passed(True, metrics, 'body')


@pytest.mark.parametrize('label', ['-Alpha-', 'Alpha%20Beta'])
def test_mixed_native_literal_names_round_trip(label):
    artifact = '- [First House](/item/1)\n<a href="/item/2">' + label + '</a>'
    metrics = score_attempt(composite_case(group(minimum=2)), artifact.encode(), ok=True)
    assert task_passed(True, metrics, 'body')
    assert {row['title'] for row in metrics['composite_group_results'][0]['records']} == {'First House', label}
