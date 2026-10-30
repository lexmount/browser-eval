"""Consecutive fields belong to one visible semantic card."""
import html

import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt


FAMILIES = {
    'earthquake': (['4.1', 'Pacific coast', '2026-01-01T12:00:00Z', '12 km'],
                   ['5.2', 'Northern coast', '2026-01-02T12:00:00Z', '18 km'], 2),
    'quote': (['“A new thought.”', 'by Alice', 'Tags: nature life'],
              ['“Another thought.”', 'by Bob', 'Tags: learning books'], 1),
    'api_operation': (['GET /items', 'List current items'],
                      ['POST /items', 'Create an item'], 1),
}


def card(lines, tag='article'):
    return '<' + tag + '>' + ''.join('<p>' + html.escape(line) + '</p>' for line in lines) + '</' + tag.split()[0] + '>'


def row(identifier, schema, source, expected, *, representation='html', count=None, minimum=1, maximum=1):
    contract = {'version': 1, 'schema': schema, 'minimum_records': minimum, 'stable_text_anchors': []}
    if maximum is not None:
        contract['maximum_records'] = maximum
    return {'id': 'native-card-family-' + schema + '-' + identifier,
            'category': 'structured-extraction', 'root_cause': '4409052356',
            'failure_mode': 'false-negative' if expected else 'false-positive',
            'case': {'url': 'https://example.test/records', 'acceptance_contract': contract},
            'artifact': source, 'ok': True,
            'metadata': {'answer_format': representation} if representation else {},
            'expected_task_pass': expected,
            **({'expected_metrics': {'stable_complete_record_count': count}} if count is not None else {})}


def controls():
    for schema, (first, second, split) in FAMILIES.items():
        fragments = card(first[:split]) + card(first[split:])
        for representation in ['html', None]:
            suffix = representation or 'inferred'
            yield row('complete-' + suffix, schema, card(first), True, representation=representation, count=1)
            yield row('split-' + suffix, schema, fragments, False, representation=representation, count=0)
            yield row('adjacent-' + suffix, schema, card(first) + card(second), True,
                      representation=representation, minimum=2, maximum=2, count=2)
            yield row('adjacent-over-limit-' + suffix, schema, card(first) + card(second), False,
                      representation=representation, count=2)
            yield row('mixed-cannot-invent-second-' + suffix, schema, card(second) + fragments, False,
                      representation=representation, minimum=2, maximum=None, count=1)
            yield row('mixed-keeps-complete-' + suffix, schema, card(second) + fragments, True,
                      representation=representation, maximum=None, count=1)
        for tag in ['li', 'div role="listitem"']:
            prefix = '<ul>' if tag == 'li' else '<div role="list">'
            suffix = '</ul>' if tag == 'li' else '</div>'
            yield row('list-complete-' + tag.split()[0], schema, prefix + card(first, tag) + suffix, True, count=1)
            yield row('list-split-' + tag.split()[0], schema,
                      prefix + card(first[:split], tag) + card(first[split:], tag) + suffix, False, count=0)
        yield row('nested-split', schema,
                  card(first[:split]).replace('</article>', card(first[split:]) + '</article>'), False, count=0)
        yield row('nested-complete', schema,
                  card(first).replace('</article>', card(second) + '</article>'), True,
                  minimum=2, maximum=2, count=2)
        yield row('presentation-wrappers', schema,
                  '<article><section>' + card(first[:split], 'div') + '</section><div>'
                  + card(first[split:], 'div') + '</div></article>', True, count=1)
        yield row('hidden-complete-cannot-rescue-split', schema,
                  card(second).replace('<article>', '<article hidden>') + fragments, False, count=0)
        yield row('markdown-complete', schema, '\n'.join(first), True, representation='markdown', count=1)
        yield row('markdown-adjacent', schema, '\n'.join(first + second), True,
                  representation='markdown', minimum=2, maximum=2, count=2)
        yield row('layout-cell-complete', schema, '<table><tr><td>' + card(first) + '</td></tr></table>', True, count=1)
        yield row('layout-cell-split', schema, '<table><tr><td>' + fragments + '</td></tr></table>', False, count=0)

    # Existing record families retain their own card/line owners.
    for schema, source in [
        ('product', '<article><p>Coat</p><p>$19</p></article>'),
        ('catalog_product', '<article><h2>Coat</h2><p>$19</p><p>Warm winter coat</p><p>3 reviews</p></article>'),
        ('review', '<article><p>2026-01-01</p><p>Useful response</p></article>'),
        ('name_description', '<article><h2>Coat</h2><p>Warm winter coat</p></article>'),
        ('taxonomy_family', '<article><p>Newidae</p></article>'),
    ]:
        yield row('existing-complete', schema, source, True, count=1)


CONTROLS = list(controls())


@pytest.mark.parametrize('control', CONTROLS, ids=[control['id'] for control in CONTROLS])
def test_semantic_card_fields_through_public_scorer(control):
    metrics = score_attempt(control['case'], control['artifact'].encode(), ok=True, metadata=control['metadata'])
    assert task_passed(True, metrics, 'body') is control['expected_task_pass']
    for key, expected in control.get('expected_metrics', {}).items():
        assert metrics[key] == expected
