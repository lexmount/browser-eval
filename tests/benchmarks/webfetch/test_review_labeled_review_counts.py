"""An owned numeric review count is distinct from an unlabeled page number."""
import html

import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt


def controls():
    values = [('labeled', 'Reviews: 5', True, '5'),
              ('zero', 'REVIEWS : 0', True, '0'),
              ('labeled-suffix', 'Reviews: 5 reviews', True, '5'),
              ('native', '5 reviews', True, '5'),
              ('singular', '1 review', True, '1'),
              ('bare-number', '5', False, None),
              ('negative', 'Reviews: -1', False, None),
              ('fraction', 'Reviews: 5.5', False, None),
              ('word', 'Reviews: five', False, None),
              ('rating', 'Reviews: 5 stars', False, None)]
    for label, value, expected, count in values:
        lines = ['Title: Coat', 'Price: $19', 'Description: Warm winter coat', value]
        for representation in ['markdown', 'html', 'plain_text', None]:
            source = ('<article>' + ''.join('<p>' + html.escape(line) + '</p>' for line in lines) + '</article>'
                      if representation == 'html' else '\n'.join(lines))
            metrics = {'stable_complete_record_count': int(expected)}
            if expected:
                metrics['stable_records'] = [{'fields': {'title': 'Coat', 'price': '$19',
                    'description': 'Warm winter coat', 'reviews': count}, 'valid': True, 'errors': []}]
            yield {'id': 'labeled-reviews-' + label + '-' + str(representation),
                'category': 'structured-extraction',
                'failure_mode': 'false-negative' if expected else 'false-positive',
                'root_cause': 'Explicit review labels own numeric counts; unlabelled numbers cannot supply a catalog review field.',
                'case': {'url': 'https://example.test/catalog', 'acceptance_contract': {
                    'version': 1, 'schema': 'catalog_product', 'minimum_records': 1}},
                'artifact': source, 'metadata': {'answer_format': representation} if representation else {},
                'ok': True, 'expected_task_pass': expected, 'expected_metrics': metrics}


CONTROLS = list(controls())


@pytest.mark.parametrize('control', CONTROLS, ids=lambda value: value['id'])
def test_labeled_counts_share_the_field_contract_without_accepting_bare_numbers(control):
    measured = score_attempt(control['case'], control['artifact'].encode(), ok=True, metadata=control['metadata'])
    assert task_passed(True, measured) is control['expected_task_pass']
    for key, value in control['expected_metrics'].items():
        assert measured[key] == value


def test_numeric_labels_and_table_columns_are_equivalent_structured_anchors():
    case = {'url': 'https://example.test/catalog', 'acceptance_contract': {
        'version': 1, 'schema': 'catalog_product', 'minimum_records': 1,
        'stable_text_anchors': [{'id': 'product', 'alternatives': [
            '|title|price|description|reviews|\n|---|---|---|---|\n|Coat|$19|Warm winter coat|5|']} ]}}
    source = 'Title: Coat\nPrice: $19\nDescription: Warm winter coat\nReviews: 5'
    measured = score_attempt(case, source.encode(), ok=True, metadata={'answer_format': 'markdown'})
    assert task_passed(True, measured)
    assert measured['stable_anchor_coverage'] == 1
