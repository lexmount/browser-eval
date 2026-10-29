"""Quote delimiters and excluded anchor text retain their record ownership."""
import html

import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt
from lexbench_webfetch.stable_tasks import _structured_record_view, source_record_hash


def table(fields, native=False):
    if native:
        return '<table><tr>' + ''.join('<th>' + key + '</th>' for key in fields) + '</tr><tr>' + ''.join(
            '<td>' + html.escape(value) + '</td>' for value in fields.values()) + '</tr></table>'
    return '| ' + ' | '.join(fields) + ' |\n| ' + ' | '.join('---' for _ in fields) + ' |\n| ' + ' | '.join(fields.values()) + ' |'


def control(identifier, cause, schema, source, expected, *, representation='markdown', fields=None,
            anchor=None, exclusions=None, minimum=1, extra=None, count=None):
    contract = {'version': 1, 'schema': schema, 'minimum_records': minimum,
                'stable_text_anchors': [{'id': 'record', 'alternatives': [anchor]}] if anchor else [], **(extra or {})}
    if exclusions:
        contract['text_exclusions'] = exclusions
    metrics = {}
    if fields is not None:
        metrics['stable_records'] = [{'fields': fields, 'valid': True, 'errors': []}]
    if count is not None:
        metrics['stable_complete_record_count'] = count
    return {'id': identifier, 'root_cause': cause, 'category': 'structured-extraction',
            'failure_mode': 'false-negative' if expected else 'false-positive',
            'case': {'url': 'https://example.test/list', 'acceptance_contract': contract},
            'artifact': source, 'metadata': {'answer_format': representation}, 'ok': True,
            'expected_task_pass': expected, **({'expected_metrics': metrics} if metrics else {})}


def quote_controls():
    for index, quote in enumerate(['Stand by me', 'Stand — by me', 'Tags: friendship — by the sea',
                                    'A "word" by the sea', 'By and by, words matter']):
        fields = {'quote': quote, 'author': 'Alice', 'tags': 'friendship'}
        source = '“' + quote + '” by Alice Tags: friendship'
        for representation, rendered in [('markdown', source), ('plain_text', source),
                                          ('html', '<article><p>' + html.escape(source) + '</p></article>')]:
            yield control('quote-owner-' + str(index) + '-' + representation, '4409310330', 'quote', rendered,
                          True, representation=representation, fields=fields, anchor=table(fields))
        names = sorted(fields)
        membership = {'kind': 'fixed', 'fields': names, 'record_hashes': [source_record_hash(fields, names, 'quote')]}
        yield control('quote-owner-hash-' + str(index), '4409310330', 'quote', source, True,
                      fields=fields, extra={'source_membership': membership})
    gold = {'quote': 'Stand by me', 'author': 'Alice', 'tags': 'friendship'}
    shortened = {'quote': 'Stand', 'author': 'me” by Alice', 'tags': 'friendship'}
    names = sorted(gold)
    yield control('quote-owner-explicit-columns', '4409310330', 'quote', table(gold), True,
                  fields=gold, anchor=table(gold))
    yield control('quote-owner-shortened-hash', '4409310330', 'quote',
                  '“Stand by me” by Alice Tags: friendship', False,
                  extra={'source_membership': {'kind': 'fixed', 'fields': names,
                      'record_hashes': [source_record_hash(shortened, names, 'quote')]}})
    for identifier, anchor, expected in [('correct', table(gold), True), ('shortened', table(shortened), False)]:
        yield control('quote-owner-anchor-' + identifier, '4409310330', 'quote',
                      '“Stand by me” by Alice Tags: friendship', expected, anchor=anchor)
    for identifier, source in [('unquoted', 'Quote: Stand by me by Alice Tags: friendship'),
                               ('author-label', '“Stand by me” Author: Alice Tags: friendship'),
                               ('dash-author', '“Stand by me” — by Alice Tags: friendship'),
                               ('ascii', '"Stand by me" by Alice Tags: friendship'),
                               ('single', '‘Stand by me’ by Alice Tags: friendship')]:
        yield control('quote-owner-' + identifier, '4409310330', 'quote', source, True, fields=gold)
    yield control('quote-owner-unclosed', '4409310330', 'quote', '“Stand by me by Alice Tags: friendship', False)
    yield control('quote-owner-distinct', '4409310330', 'quote',
                  '“Stand by me” by Alice Tags: friendship\n“Stand by you” by Alice Tags: friendship', True,
                  minimum=2, count=2)


FAMILIES = {
    'product': ({'name': 'Widget', 'price': '$10'}, 'Widget\n$10', 'Widget'),
    'catalog_product': ({'title': 'Widget', 'price': '$10', 'description': 'Useful device', 'reviews': '3 reviews'},
                        'Widget\n$10\nUseful device\n3 reviews', 'Widget'),
    'earthquake': ({'magnitude': '4.1', 'location': 'Pacific coast', 'time': '2026-01-01T12:00:00Z', 'depth': '12 km'},
                   '4.1\nPacific coast\n2026-01-01T12:00:00Z\n12 km', 'Pacific coast'),
    'quote': ({'quote': 'Stand by me', 'author': 'Alice', 'tags': 'friendship'},
              '“Stand by me” by Alice Tags: friendship', 'Stand by me'),
    'review': ({'date': '2026-01-01', 'text': 'Useful response'}, '2026-01-01\nUseful response', 'Useful response'),
    'api_operation': ({'method': 'GET', 'path': '/items', 'summary': 'List current items'},
                      'GET /items\nList current items', 'List current items'),
    'name_description': ({'name': 'Widget', 'description': 'Useful device'}, 'Widget\nUseful device', 'Widget'),
    'taxonomy_family': ({'family': 'Newidae'}, 'Newidae', 'Newidae'),
    'table_row': ({'name': 'Widget', 'price': '$10'}, 'Widget\n$10', 'Widget'),
}

INVARIANTS = [
    ('inline-word', '<article><p>Wid<strong>get</strong></p><p>$10</p></article>', 'Widget'),
    ('link-punctuation', '<article><p><a href="/item">Widget</a>—<span>Pro</span></p><p>$10</p></article>', 'Widget—Pro'),
    ('pre-price', '<article><p>Widget</p><pre>  $10  </pre></article>', 'Widget'),
    ('table-spaces', '<table><tr><th>name</th><th>price</th></tr><tr><td>Widget   Pro</td><td>$10</td></tr></table>', 'Widget   Pro'),
    ('table-pre', '<table><tr><th>name</th><th>price</th></tr><tr><td><pre>Widget \n Pro</pre></td><td>$10</td></tr></table>', 'Widget \n Pro'),
    ('table-literal', '<table><tr><th>name</th><th>price</th></tr><tr><td><code>[Widget](/other)</code></td><td>$10</td></tr></table>', '[Widget](/other)'),
]


def anchor_controls():
    for identifier, source, name in INVARIANTS:
        fields = {'name': name, 'price': '$10'}
        yield control('anchor-scope-no-match-' + identifier, '4409310348', 'product', source, True,
                      representation='html', anchor=table(fields, True), fields=fields,
                      exclusions={'text_patterns': ['NeverMatchingToken']})
    for schema, (fields, flat, pattern) in FAMILIES.items():
        extra = ({'table_columns': [{'name': 'name', 'label': 'name', 'type': 'text'},
                                   {'name': 'price', 'label': 'price', 'type': 'currency_usd'}],
                  'record_identity_fields': ['name'],
                  'table_scope': {'row_selector': 'table tr', 'cell_selector': 'td',
                                  'start_pattern': '^name$', 'end_pattern': '^Footer$',
                                  'plain_row_pattern': r'(?P<name>[^\t]+)\t(?P<price>\$\d+)'}}
                 if schema == 'table_row' else {})
        native, markdown = table(fields, True), table(fields)
        other_fields = {key: value.replace('Widget', 'Other').replace('Pacific', 'Northern')
                        .replace('Stand by me', 'Another thought').replace('Newidae', 'Oldidae')
                        .replace('2026-01-01', '2026-01-02').replace('/items', '/other')
                        for key, value in fields.items()}
        other = table(other_fields, True)
        css = {'html_selectors': ['.excluded']}
        for label, source, expected, count in [
            ('excluded', '<div class="excluded">' + native + '</div>', False, 1),
            ('included', '<main>' + native + '</main>', True, 1),
            ('mixed-excluded', '<div class="excluded">' + native + '</div><main>' + other + '</main>', False, 2),
            ('mixed-included', '<div class="excluded">' + other + '</div><main>' + native + '</main>', True, 2),
        ]:
            yield control('anchor-scope-' + schema + '-css-' + label, '4409310348', schema, source,
                          expected, representation='html', anchor=markdown, exclusions=css, extra=extra, count=count)
        for representation, source in [('html', native), ('markdown', markdown), *([] if schema == 'table_row' else [('plain_text', flat)])]:
            for excluded in [False, True]:
                yield control('anchor-scope-' + schema + '-text-' + representation + '-' + str(excluded),
                              '4409310348', schema, source, not excluded, representation=representation,
                              anchor=flat if representation == 'plain_text' else markdown,
                              exclusions={'text_patterns': [pattern if excluded else 'Absent token']},
                              extra=extra, count=1)
    for identifier, source, pattern in [
        ('reported-card', '<div class="excluded">Widget<br>$10</div>', None),
        ('inline-split', '<article><p>Wid<strong>get</strong></p><p>$10</p></article>', 'Widget'),
        ('cross-field', '<article><p>Widget</p><p>$10</p></article>', r'Widget\s+\$10'),
        ('encoded', '<article><p>Wid&#103;et</p><p>$10</p></article>', 'Widget'),
    ]:
        yield control('anchor-scope-product-' + identifier, '4409310348', 'product', source, False,
                      representation='html', anchor='Widget\n$10', count=1,
                      exclusions={'text_patterns': [pattern]} if pattern else {'html_selectors': ['.excluded']})
    # Link-family anchors use their filtered literal-label owner already.
    for schema in ['named_link', 'linked_product']:
        extra = {'hosts': ['example.test'], 'entry_path': r'/item/[a-z]+'}
        price = '<p>$10</p>' if schema == 'linked_product' else ''
        if schema == 'linked_product':
            extra.update(price_patterns=[r'\$\d+'], price_exclusion_patterns=[])
        native = '<article><a href="https://example.test/item/widget">Widget</a>' + price + '</article>'
        markdown = '[Widget](https://example.test/item/widget)' + ('\n$10' if price else '')
        for excluded in [False, True]:
            yield control('anchor-scope-' + schema + '-css-' + str(excluded), '4409310348', schema,
                          '<div class="' + ('excluded' if excluded else 'included') + '">' + native + '</div>',
                          not excluded, representation='html', anchor='Widget',
                          exclusions={'html_selectors': ['.excluded']}, extra=extra, count=1)
            for representation, source in [('html', native), ('markdown', markdown)]:
                yield control('anchor-scope-' + schema + '-text-' + representation + '-' + str(excluded),
                              '4409310348', schema, source, not excluded, representation=representation,
                              anchor='Widget', exclusions={'text_patterns': ['Widget' if excluded else 'Absent token']},
                              extra=extra, count=1)


QUOTE_CONTROLS = list(quote_controls())
ANCHOR_CONTROLS = list(anchor_controls())


@pytest.mark.parametrize('row', QUOTE_CONTROLS + ANCHOR_CONTROLS,
                         ids=[row['id'] for row in QUOTE_CONTROLS + ANCHOR_CONTROLS])
def test_quote_and_anchor_scope_through_public_scorer(row):
    metrics = score_attempt(row['case'], row['artifact'].encode(), ok=True, metadata=row['metadata'])
    assert task_passed(True, metrics, 'body') is row['expected_task_pass']
    for key, expected in row.get('expected_metrics', {}).items():
        assert metrics[key] == expected


@pytest.mark.parametrize('identifier,source,name', INVARIANTS)
def test_nonmatching_exclusions_preserve_owned_fields_validity_hashes_and_anchors(identifier, source, name):
    fields = {'name': name, 'price': '$10'}
    contract = {'version': 1, 'schema': 'product', 'minimum_records': 1,
                'stable_text_anchors': [{'id': 'record', 'alternatives': [table(fields, True)]}]}
    before = _structured_record_view(source, contract, representation='html')
    after = _structured_record_view(source, contract, representation='html', text_patterns=['NeverMatchingToken'])
    assert before == after == [{'fields': fields, 'valid': True, 'errors': []}]
    assert [source_record_hash(record['fields'], sorted(fields), 'product') for record in before] == [
        source_record_hash(record['fields'], sorted(fields), 'product') for record in after]
    outputs = []
    for exclusions in [{}, {'text_patterns': ['NeverMatchingToken']}]:
        case = {'url': 'https://example.test/', 'acceptance_contract': {**contract, 'text_exclusions': exclusions}}
        metrics = score_attempt(case, source.encode(), ok=True, metadata={'answer_format': 'html'})
        outputs.append((metrics['stable_records'], metrics['stable_anchor_results'], task_passed(True, metrics, 'body')))
    assert outputs[0] == outputs[1]
    assert outputs[1][-1]
