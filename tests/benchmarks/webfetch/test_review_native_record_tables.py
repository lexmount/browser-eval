"""Native tables retain literal cells and the same typed record requirements."""
import copy
import html

import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt


FAMILIES = {
    'product': {'name': 'Coat', 'price': '$19'},
    'catalog_product': {'title': 'Coat', 'price': '$19', 'description': 'Warm winter coat', 'reviews': '3 reviews'},
    'quote': {'quote': 'A new thought', 'author': 'Alice', 'tags': 'nature life'},
    'review': {'date': '2026-01-01', 'text': 'Useful response'},
    'name_description': {'name': 'Coat', 'description': 'Warm winter coat'},
    'api_operation': {'method': 'GET', 'path': '/items', 'summary': 'List current items'},
    'taxonomy_family': {'family': 'Newidae'},
    'earthquake': {'magnitude': '4.1', 'location': 'Pacific coast', 'time': '2026-01-01T12:00:00Z', 'depth': '12 km'},
}


def native_table(fields, *, header_tag='th'):
    return ('<table><tr>' + ''.join('<' + header_tag + '>' + key + '</' + header_tag + '>' for key in fields)
            + '</tr><tr>' + ''.join('<td>' + html.escape(value) + '</td>' for value in fields.values())
            + '</tr></table>')


def task(schema='catalog_product', **extra):
    return {'url': 'https://example.test/catalog', 'acceptance_contract': {
        'version': 1, 'schema': schema, 'minimum_records': 1, 'maximum_records': 1,
        'stable_text_anchors': [], **extra}}


def controls():
    for schema, fields in FAMILIES.items():
        native = native_table(fields)
        markdown = ('| ' + ' | '.join(fields) + ' |\n| ' + ' | '.join('---' for _ in fields)
                    + ' |\n| ' + ' | '.join(fields.values()) + ' |')
        for representation, source in [('html', native), (None, native), ('markdown', markdown)]:
            yield row(schema + '-' + (representation or 'inferred'), task(schema), source, True, representation,
                      {'stable_records': [{'fields': fields, 'valid': True, 'errors': []}]})
        incomplete = native.replace('<td>' + html.escape(next(iter(fields.values()))) + '</td>', '<td></td>', 1)
        yield row(schema + '-incomplete', task(schema), incomplete, False, 'html')

    fields = FAMILIES['catalog_product']
    native = native_table(fields)
    header, body = native.split('</tr>', 1)
    cases = [
        ('header-only', header + '</tr></table>', False),
        ('hidden-row', native.replace('<tr><td>', '<tr hidden><td>'), False),
        ('hidden-field', native.replace('<td>3 reviews', '<td hidden>3 reviews'), False),
        ('hidden-table', native.replace('<table>', '<table style="display:none">'), False),
        ('duplicate-header', native.replace('<th>reviews</th>', '<th>price</th><th>reviews</th>')
         .replace('<td>3 reviews</td>', '<td>$20</td><td>3 reviews</td>'), False),
        ('wrong-price', native.replace('<td>$19</td>', '<td>**$19**</td>'), False),
        ('wrong-reviews', native.replace('3 reviews', 'many reviews'), False),
        ('header-literal-markdown', native.replace('<th>title</th>', '<th>**title**</th>'), False),
        ('missing-header', native.replace('<th>price</th>', '<th>cost estimate</th>'), False),
        ('thead-td', native.replace('<table><tr>', '<table><thead><tr>', 1)
         .replace('</tr><tr>', '</tr></thead><tbody><tr>', 1).replace('</table>', '</tbody></table>')
         .replace('<th>', '<td>').replace('</th>', '</td>'), True),
        ('header-repeat', header + '</tr>' + header.replace('<table>', '') + '</tr>' + body, True),
        ('split-rows', header + '</tr><tr><td>Coat</td><td>$19</td><td></td><td></td></tr>'
         '<tr><td></td><td></td><td>Warm winter coat</td><td>3 reviews</td></tr></table>', False),
        ('split-tables', header + '</tr></table><table>' + body, False),
        ('nested-price', native.replace('<td>$19</td>', '<td><table><tr><td>$19</td></tr></table></td>'), False),
        ('span-price', native.replace('<td>$19</td>', '<td colspan="2">$19</td>'), False),
        ('native-formatting', native.replace('<td>Coat</td>', '<td><b>Coat</b></td>')
         .replace('<td>$19</td>', '<td><a href="/price">$19</a></td>'), True),
        ('cell-markdown-table', '<table><tr><th>Other</th></tr><tr><td><pre>'
         + html.escape('| title | price | description | reviews |\n| --- | --- | --- | --- |\n'
                       '| Coat | $19 | Warm winter coat | 3 reviews |') + '</pre></td></tr></table>', False),
        ('cell-json', '<table><tr><th>Other</th></tr><tr><td>'
         + html.escape('{"title":"Coat","price":"$19","description":"Warm winter coat","reviews":"3"}')
         + '</td></tr></table>', False),
    ]
    for identifier, source, expected in cases:
        yield row(identifier, task(), source, expected, 'html')
    literal_fields = {**fields, 'title': '[Coat | Jacket](/item/1)',
                      'description': '**Warm** | winter `coat` &amp; <b>sample</b>'}
    yield row('literal-cell-payload', task(), native_table(literal_fields), True, 'html',
              {'stable_records': [{'fields': literal_fields, 'valid': True, 'errors': []}]})
    yield row('plain-html-is-literal', task(), native, False, 'plain_text')
    reordered = dict(reversed(list(fields.items())))
    yield row('reordered-columns', task(), native_table(reordered), True, 'html')
    yield row('native-reference-anchor', task(stable_text_anchors=[{'id': 'record', 'alternatives': [native]}]),
              native, True, 'html')
    yield row('headerless-native-card', task('product'),
              '<table><tr><td><article><h2>Coat</h2><p>$19</p></article></td></tr></table>',
              True, 'html', {'stable_records': [{'fields': FAMILIES['product'], 'valid': True, 'errors': []}]})
    yield row('headerless-rows-cannot-borrow', task('product'),
              '<table><tr><td><article>Coat</article></td></tr><tr><td>$19</td></tr></table>', False, 'html')
    card = 'Title: Coat<br>Price: $19<br>Description: Warm winter coat<br>Reviews: 3 reviews'
    yield row('headerless-labelled-cell-card', task(), '<table><tr><td>' + card + '</td></tr></table>', True, 'html')
    yield row('headerless-cells-cannot-borrow', task(),
              '<table><tr><td>Title: Coat<br>Price: $19</td>'
              '<td>Description: Warm winter coat<br>Reviews: 3 reviews</td></tr></table>', False, 'html')


def row(identifier, case, source, expected, representation, expected_metrics=None):
    return {'id': 'record-table-' + identifier, 'category': 'structured-extraction',
            'failure_mode': 'false-negative' if expected else 'false-positive',
            'root_cause': '4408924388', 'case': copy.deepcopy(case), 'artifact': source, 'ok': True,
            'metadata': {'answer_format': representation} if representation else {},
            'expected_task_pass': expected, **({'expected_metrics': expected_metrics} if expected_metrics else {})}


CONTROLS = list(controls())


@pytest.mark.parametrize('control', CONTROLS, ids=[r['id'] for r in CONTROLS])
def test_native_record_table_ownership_through_public_scorer(control):
    metrics = score_attempt(control['case'], control['artifact'].encode(), ok=True,
                            metadata=control['metadata'])
    assert task_passed(True, metrics, 'body') is control['expected_task_pass']
    for key, expected in control.get('expected_metrics', {}).items():
        assert metrics[key] == expected
