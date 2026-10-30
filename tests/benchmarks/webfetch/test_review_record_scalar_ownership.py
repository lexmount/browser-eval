"""Render owned inline scalars once and keep consumed regions as record stops."""
import html

import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt
from lexbench_webfetch.stable_tasks import source_record_hash


FAMILIES = {
    'product': (['Coat', '$19'], {'name': 'Coat', 'price': '$19'}, 1),
    'catalog_product': (['Coat', '$19', 'Warm winter coat', '3 reviews'],
                        {'title': 'Coat', 'price': '$19', 'description': 'Warm winter coat', 'reviews': '3'}, 2),
    'earthquake': (['4.1', 'Pacific coast', '2026-01-01T12:00:00Z', '12 km'],
                   {'magnitude': '4.1', 'location': 'Pacific coast', 'time': '2026-01-01T12:00:00Z', 'depth': '12 km'}, 2),
    'quote': (['“A new thought.”', 'by Alice', 'Tags: nature life'],
              {'quote': 'A new thought.', 'author': 'Alice', 'tags': 'nature life'}, 1),
    'review': (['2026-01-01', 'Useful response'], {'date': '2026-01-01', 'text': 'Useful response'}, 1),
    'api_operation': (['GET', '/items', 'List current items'],
                      {'method': 'GET', 'path': '/items', 'summary': 'List current items'}, 2),
    'name_description': (['Coat', 'Warm winter coat'], {'name': 'Coat', 'description': 'Warm winter coat'}, 1),
    'taxonomy_family': (['Newidae'], {'family': 'Newidae'}, 1),
}


def control(identifier, schema, source, expected, cause, *, representation='markdown', minimum=1,
            count=None, fields=None, membership=None):
    contract = {'version': 1, 'schema': schema, 'minimum_records': minimum, 'stable_text_anchors': []}
    if membership:
        names = list(membership)
        contract['source_membership'] = {'kind': 'fixed', 'fields': names,
            'record_hashes': [source_record_hash(membership, names, schema)]}
    metrics = {}
    if count is not None:
        metrics['stable_complete_record_count'] = count
    if fields is not None:
        metrics['stable_records'] = [{'fields': fields, 'valid': True, 'errors': []}]
    return {'id': 'record-scalar-' + identifier, 'category': 'structured-extraction', 'root_cause': cause,
            'failure_mode': 'false-negative' if expected else 'false-positive',
            'case': {'url': 'https://example.test/records', 'acceptance_contract': contract},
            'artifact': source, 'metadata': {'answer_format': representation}, 'ok': True,
            'expected_task_pass': expected, **({'expected_metrics': metrics} if metrics else {})}


def scalar_controls():
    for schema, (lines, fields, _) in FAMILIES.items():
        coded = '\n'.join('`' + line + '`' for line in lines)
        native = '<article>' + ''.join('<p><code>' + html.escape(line) + '</code></p>' for line in lines) + '</article>'
        for representation, source, expected in [('markdown', coded, True), ('html', native, True),
                                                  ('plain_text', '\n'.join(lines), True)]:
            yield control(schema + '-' + representation, schema, source, expected, '4409157392',
                          representation=representation, fields=fields)
        # Backticks in plain text are actual data, not source delimiters.
        if schema in {'product', 'catalog_product', 'earthquake', 'review', 'api_operation', 'taxonomy_family'}:
            yield control(schema + '-plain-backticks', schema, coded, False, '4409157392', representation='plain_text')
    for identifier, source in [
        ('path', 'GET `/items` List current items'),
        ('method', '`GET` /items List current items'),
        ('method-path', '`GET /items` List current items'),
        ('separate', '`GET` `/items` `List current items`'),
    ]:
        yield control('api-' + identifier, 'api_operation', source, True, '4409157392', fields=FAMILIES['api_operation'][1])
    for identifier, price in [('html', '<strong>$19</strong>'), ('emphasis', '**$19**'),
                              ('link', '[$19](/price)'), ('entity', '&#36;19'), ('escape', r'\$19')]:
        for representation, source in [('markdown', 'Coat\n`' + price + '`'),
                                       ('html', '<article><p>Coat</p><p><code>' + html.escape(price) + '</code></p></article>')]:
            yield control('literal-price-' + identifier + '-' + representation, 'product', source, False,
                          '4409157392', representation=representation, count=0)
    for identifier, name, coded in [
        ('link', '[Coat](/other)', '`[Coat](/other)`'),
        ('html', '<strong>Coat</strong>', '`<strong>Coat</strong>`'),
        ('heading', '# Coat', '`# Coat`'),
        ('emphasis', '**Coat**', '`**Coat**`'),
        ('backtick', '`Coat`', '`` `Coat` ``'),
    ]:
        fields = {'name': name, 'price': '$19'}
        yield control('literal-name-' + identifier, 'product', coded + '\n`$19`', True, '4409157392',
                      membership=fields, fields=fields)
        yield control('literal-name-shortened-' + identifier, 'product', 'Coat\n$19', False, '4409157392', membership=fields)
    for identifier, source in [('escaped-backticks', 'Coat\n\\`$19\\`'),
                               ('entity-backticks', 'Coat\n&#96;$19&#96;'),
                               ('inline-json', '`{"name":"Coat","price":"$19"}`'),
                               ('code-header', '`| name | price |`\n| --- | --- |\n| Coat | $19 |')]:
        yield control(identifier, 'product', source, False, '4409157392', count=0)
    yield control('code-cell-pipe', 'product', '| `name` | `price` |\n| --- | --- |\n| `Coat | Jacket` | `$19` |',
                  True, '4409157392', fields={'name': 'Coat | Jacket', 'price': '$19'})
    yield control('inline-newline-no-new-records', 'product', '`Alpha\n$10\nBeta\n$20`', False,
                  '4409157392', minimum=2, count=1)
    yield control('summary-literal-html', 'api_operation', 'GET `/items`\n`<strong>List</strong> [items](/docs)`',
                  True, '4409157392', fields={'method': 'GET', 'path': '/items', 'summary': '<strong>List</strong> [items](/docs)'})


def table_controls():
    reported_table = '| Name | Price |\n| --- | --- |\n| Beta | $20 |'
    yield control('consumed-table-reported-split', 'product',
                  'Alpha\n\n' + reported_table + '\n\n$10', False, '4409157399', minimum=2, count=1)
    yield control('consumed-table-reported-complete', 'product',
                  'Alpha\n$10\n\n' + reported_table, True, '4409157399', minimum=2, count=2)
    for schema, (lines, fields, split) in FAMILIES.items():
        if schema == 'taxonomy_family':
            continue  # One-line records cannot borrow a second field.
        table_fields = {key: value.replace('Coat', 'Beta').replace('items', 'other').replace('4.1', '5.2')
                        .replace('Alice', 'Bob').replace('thought', 'statement')
                        .replace('2026-01-01', '2026-01-02') for key, value in fields.items()}
        table = ('| ' + ' | '.join(table_fields) + ' |\n| ' + ' | '.join('---' for _ in fields)
                 + ' |\n| ' + ' | '.join(table_fields.values()) + ' |')
        before, after = '\n'.join(lines[:split]), '\n'.join(lines[split:])
        for identifier, source, expected, count in [
            ('split', before + '\n\n' + table + '\n\n' + after, False, 1),
            ('complete-before', '\n'.join(lines) + '\n\n' + table, True, 2),
            ('complete-after', table + '\n\n' + '\n'.join(lines), True, 2),
        ]:
            yield control('consumed-table-' + schema + '-' + identifier, schema, source, expected,
                          '4409157399', minimum=2, count=count)


SCALAR_CONTROLS = list(scalar_controls())
TABLE_CONTROLS = list(table_controls())


@pytest.mark.parametrize('row', SCALAR_CONTROLS + TABLE_CONTROLS,
                         ids=[row['id'] for row in SCALAR_CONTROLS + TABLE_CONTROLS])
def test_record_scalar_and_consumed_region_ownership(row):
    metrics = score_attempt(row['case'], row['artifact'].encode(), ok=True, metadata=row['metadata'])
    assert task_passed(True, metrics, 'body') is row['expected_task_pass']
    for key, expected in row.get('expected_metrics', {}).items():
        assert metrics[key] == expected
