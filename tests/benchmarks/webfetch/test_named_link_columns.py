"""Public-scoring controls for native named-list table ownership."""
import pytest
from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt


def case():
    return {
        'url': 'https://example.test/catalog', 'task_kind': 'body', 'min_content_chars': 1,
        'composite_acceptance': {'version': 1, 'groups': [
            {'id': 'first', 'kind': 'named_link', 'minimum_records': 2,
             'hosts': ['example.test'], 'entry_path': '/item/[0-9]+',
             'start_pattern': 'Track A', 'end_pattern': 'Track B'},
            {'id': 'second', 'kind': 'named_link', 'minimum_records': 2,
             'hosts': ['example.test'], 'entry_path': '/item/[0-9]+',
             'start_pattern': 'Track B', 'end_pattern': 'End'},
        ]},
    }


def table(layout, first=True, second=True):
    cells = [[('[Alpha](https://example.test/item/1)' if first else ''),
              ('[Gamma](https://example.test/item/3)' if second else '')],
             [('[Beta](https://example.test/item/2)' if first else ''),
              ('[Delta](https://example.test/item/4)' if second else '')]]
    if layout == 'html':
        rows = ''.join('<tr>'+''.join('<td>'+cell+'</td>' for cell in row)+'</tr>' for row in cells)
        # HTML links, rather than Markdown-in-HTML, exercise the native surface.
        import re
        rows = re.sub(r'\[([^]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', rows)
        return '<table><tr><th colspan="2">Events</th></tr><tr><th>Track A</th><th>Track B</th></tr>'+rows+'</table>'
    delimiter = '\t' if layout == 'tsv' else ' | '
    heading = delimiter.join(['Track A', 'Track B'])
    body = '\n'.join(delimiter.join(row) for row in cells)
    if layout == 'tsv':
        return heading+'\n'+body
    heading = '| '+heading+' |'
    body = '\n'.join('| '+delimiter.join(row)+' |' for row in cells)
    if layout == 'caption_before':
        return '| Events |\n| --- |\n'+heading+'\n'+body
    return heading+'\n| --- | --- |\n'+body


@pytest.mark.parametrize('layout', ['standard', 'caption_before', 'tsv', 'html'])
@pytest.mark.parametrize('first,second', [(True, True), (False, True), (True, False), (False, False)])
def test_named_columns_cannot_borrow_navigation_or_neighbour_records(layout, first, second):
    prefix = '[A](https://example.test/division-a "Track A")\n'
    prefix += '[Preparation](https://example.test/item/11)\n[Resources](https://example.test/item/12)\n\n'
    suffix = '\n\n[State One](https://example.test/item/21)\n[State Two](https://example.test/item/22)\n# End'
    content = prefix+table(layout, first, second)+suffix
    if layout == 'html':
        content = html_document(table(layout, first, second))
    metrics = score_attempt(case(), content.encode(), ok=True)
    assert task_passed(True, metrics) is (first and second)
    counts = [group['count'] for group in metrics['composite_group_results']]
    if layout != 'html' or (first and second):
        assert counts == [2 if first else 0, 2 if second else 0]
    else:
        assert all(count <= 2 for count in counts)
        assert all(present or count == 0 for present, count in zip((first, second), counts))


@pytest.mark.parametrize('layout', ['standard', 'caption_before', 'tsv', 'html'])
def test_empty_named_table_cannot_fall_back_to_surrounding_lists(layout):
    content = '[A](https://example.test/division-a "Track A")\n'
    content += '[Preparation](https://example.test/item/11)\n[Resources](https://example.test/item/12)\n\n'
    empty = table(layout).splitlines()
    if layout == 'html':
        import re
        body = re.sub(r'<tr><td>.*?</tr>', '', table(layout))
    elif layout == 'standard':
        body = '\n'.join(empty[:2])
    elif layout == 'caption_before':
        body = '\n'.join(empty[:3])
    else:
        body = empty[0]
    content += body+'\n\n[State One](https://example.test/item/21)\n[State Two](https://example.test/item/22)\n# End'
    if layout == 'html':
        content = html_document(body)
    assert not task_passed(True, score_attempt(case(), content.encode(), ok=True))


def html_document(body):
    return ('<!doctype html><html><body><nav><a href="/division-a" title="Track A">A</a></nav>'
            '<p><a href="/item/11">Preparation</a><a href="/item/12">Resources</a></p>'
            +body+'<p><a href="/item/21">State One</a><a href="/item/22">State Two</a></p>'
            '<h2>End</h2></body></html>')


@pytest.mark.parametrize('layout', ['standard', 'caption_before'])
@pytest.mark.parametrize('complete', [True, False])
def test_earlier_non_table_column_labels_do_not_hide_later_table(layout, complete):
    content = '| Track A | Track B |\nordinary navigation\n\n'
    content += '[Preparation](https://example.test/item/11)\n[Resources](https://example.test/item/12)\n\n'
    content += table(layout, complete, complete)
    content += '\n\n[State One](https://example.test/item/21)\n[State Two](https://example.test/item/22)\n# End'
    metrics = score_attempt(case(), content.encode(), ok=True)
    assert task_passed(True, metrics) is complete
    assert [group['count'] for group in metrics['composite_group_results']] == [2 if complete else 0] * 2


def test_invalid_column_labels_without_any_table_do_not_use_section_fallback():
    content = '| Track A | Track B |\nordinary navigation\n\n'
    content += '[One](https://example.test/item/11)\n[Two](https://example.test/item/12)\n# End'
    assert not task_passed(True, score_attempt(case(), content.encode(), ok=True))


def test_unrelated_separator_width_does_not_establish_column_ownership():
    content = '| Unrelated | Three | Columns |\n| --- | --- | --- |\n'
    content += table('tsv').replace('\t', ' | ')+'\n# End'
    assert not task_passed(True, score_attempt(case(), content.encode(), ok=True))
