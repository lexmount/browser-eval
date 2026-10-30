"""Inherited visibility can be restored; display and opacity still hide subtrees."""
import pytest

from lexbench_webfetch.dynamic_lists import scoped_html, visible_html_text
from lexbench_webfetch.evaluation import evaluate_response
from tests.benchmarks.webfetch.test_review_record_formats import record_task


@pytest.mark.parametrize('parent_style', ['visibility:hidden', 'visibility:hidden!important', 'visibility:collapse'])
@pytest.mark.parametrize('child_style,visible', [('visibility:visible', True), ('visibility:initial', True),
                                                ('visibility:hidden', False), ('', False)])
def test_explicit_descendant_visibility_overrides_only_inherited_visibility(parent_style, child_style, visible):
    body = (f'<div style="{parent_style}">Hidden prefix &amp; more '
            f'<span><a style="{child_style}" href="/item/1">Alpha Widget</a>'
            'Hidden suffix</span></div><p>Useful introduction</p>')
    result = evaluate_response(record_task(), body.encode())
    assert result['passed'] is visible
    projection = visible_html_text(body)
    assert ('Alpha Widget' in projection) is visible
    assert 'Hidden prefix' not in projection
    assert 'Hidden suffix' not in projection
    assert ('Alpha Widget' in scoped_html(body).get_text()) is visible


@pytest.mark.parametrize('ancestor', ['style="display:none"', 'style="opacity:0"', 'hidden'])
def test_explicit_visible_child_cannot_restore_a_removed_subtree(ancestor):
    body = f'<div {ancestor}><a style="visibility:visible" href="/item/1">Alpha Widget</a></div><p>Introduction</p>'
    assert not evaluate_response(record_task(), body.encode())['passed']


def test_source_projection_keeps_visible_record_bytes_and_hides_ancestor_markdown():
    body = ('<div style="visibility:hidden">[Wrong](/item/2) &amp; hidden\n'
            '<a style="visibility:visible" href="/item/1">Alpha<br>Widget</a>\n'
            '[Another wrong](/item/3)</div><p>Introduction</p>')
    case = record_task(maximum_records=1)
    result = evaluate_response(case, body.encode())
    assert result['passed']
    assert result['metrics']['stable_complete_record_count'] == 1
    assert result['metrics']['stable_records'][0]['fields']['title'] == 'Alpha Widget'


def test_visible_descendant_preserves_selected_ancestor_context():
    scope = {'html_selector': '.owner', 'title_selector': '.name', 'link_selector': 'a'}
    body = ('<article class="owner" style="visibility:hidden!important">Hidden prefix'
            '<a style="visibility:visible" href="/item/1"><span class="name">Alpha Widget</span></a></article>')
    assert evaluate_response(record_task(entry_scope=scope), body.encode())['passed']
