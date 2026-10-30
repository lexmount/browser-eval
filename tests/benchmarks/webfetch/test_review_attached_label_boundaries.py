"""Attached labels require a real left boundary before their own word."""
import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt


def control(identifier, text, expected, fields):
    return {'id': 'attached-label-' + identifier, 'root_cause': '4410426579',
            'category': 'structured-extraction', 'failure_mode': 'false-negative' if expected else 'false-positive',
            'case': {'url': 'https://example.test/', 'task_kind': 'body', 'expected_any': [],
                     'required_facts': [{'id': 'entry-1-' + field, 'alternatives': [value]}
                                        for field, value in fields.items()]},
            'artifact': text, 'ok': True, 'metadata': {'answer_format': 'plain_text'},
            'expected_task_pass': expected,
            'expected_metrics': {'fact_coverage': 1.0 if expected else 0.0}}


def controls():
    product = {'name': 'widget', 'price': '$10'}
    for identifier, text, expected in [
        ('filename-word', 'filenamewidget $10', False),
        ('other-word', 'filewidget $10', False),
        ('long-word', 'x' * 256 + 'namewidget $10', False),
        ('window-inside-word', 'a' * 17 + 'filenamewidget $10', False),
        ('unicode-cjk-word', '文件namewidget $10', False),
        ('unicode-latin-word', 'cafénamewidget $10', False),
        ('unicode-greek-word', 'αnamewidget $10', False),
        ('underscore-word', 'file_namewidget $10', False),
        ('attached-name', 'namewidget $10', True),
        ('attached-both-labels', 'namewidget price$10', True),
        ('explicit-labels', 'name: widget price: $10', True),
        ('compact-colons', 'name:widget price:$10', True),
        ('heading-before-label', 'Heading\nnamewidget price$10', True),
        ('long-separated-label', 'x' * 256 + ' namewidget price$10', True),
        ('unicode-separated-label', '文件 namewidget price$10', True),
        ('wrong-price', 'namewidget price$100', False),
    ]:
        yield control(identifier, text, expected, product)
    quote = {'quote': 'Hello', 'author': 'Albert', 'tags': 'science'}
    for identifier, text, expected in [
        ('attached-by', 'Hello byAlbert Tags: science', True),
        ('attached-by-and-tags', 'Hello byAlbertTags:science', True),
        ('explicit-by', 'Hello by Albert Tags: science', True),
        ('long-preface-by', 'A long unrelated heading\nHello byAlbert Tags: science', True),
        ('wrong-author', 'Hello byAlberton Tags: science', False),
        ('other-by-word', 'Hello nearbyAlbert Tags: science', False),
    ]:
        yield control(identifier, text, expected, quote)


CONTROLS = list(controls())


@pytest.mark.parametrize('row', CONTROLS, ids=[row['id'] for row in CONTROLS])
def test_attached_label_original_text_boundaries_through_public_scorer(row):
    metrics = score_attempt(row['case'], row['artifact'].encode(), ok=True, metadata=row['metadata'])
    assert task_passed(True, metrics, 'body') is row['expected_task_pass']
    assert metrics['fact_coverage'] == row['expected_metrics']['fact_coverage']
    assert all(fact['pass'] is row['expected_task_pass'] for fact in metrics['fact_results'])
