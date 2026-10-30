"""Structured fields share visible-link adjacency without losing card owners."""
import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt
from lexbench_webfetch.stable_tasks import source_record_hash


WORDS = [
    ('prefix', 'pre[fix](/details)', 'prefix'),
    ('suffix', '[read](/details)able', 'readable'),
    ('both', 'un[break](/details)able', 'unbreakable'),
    ('whole', '[readable](/details)', 'readable'),
    ('space-before', 'read [able](/details)', 'read able'),
    ('space-after', '[read](/details) able', 'read able'),
    ('adjacent-labels', '[read](/first)[able](/second)', 'read able'),
    ('multiword-label', '[Read more](/details)able', 'Read more able'),
    ('unicode', '[可](/details)读', '可读'),
]


def record(schema, word):
    if schema == 'product':
        return word + '\n$10.00', {'name': word, 'price': '$10.00'}
    if schema == 'catalog_product':
        return word + '\n$10.00\nUseful device\n3 reviews', {
            'title': word, 'price': '$10.00', 'description': 'Useful device', 'reviews': '3'}
    if schema == 'quote':
        return '“Stand by ' + word + '” by Alice Tags: friendship', {
            'quote': 'Stand by ' + word, 'author': 'Alice', 'tags': 'friendship'}
    if schema == 'earthquake':
        return '4.1\n' + word + '\n2026-01-01T12:00:00Z\n12 km', {
            'magnitude': '4.1', 'location': word, 'time': '2026-01-01T12:00:00Z', 'depth': '12 km'}
    if schema == 'api_operation':
        return 'GET /items\nA ' + word + ' result', {'method': 'GET', 'path': '/items', 'summary': 'A ' + word + ' result'}
    if schema == 'review':
        return '2026-01-01\nA ' + word + ' result', {'date': '2026-01-01', 'text': 'A ' + word + ' result'}
    return word + '\nUseful device', {'name': word, 'description': 'Useful device'}


def control(identifier, schema, source, expected, *, fields=None, reference=None, representation='markdown', minimum=1, count=None, check_fields=True):
    contract = {'version': 1, 'schema': schema, 'minimum_records': minimum, 'stable_text_anchors': []}
    if fields:
        names = sorted(fields)
        contract['source_membership'] = {'kind': 'fixed', 'fields': names,
            'record_hashes': [source_record_hash(fields, names, schema)]}
    if reference:
        contract['stable_text_anchors'] = [{'id': 'record', 'alternatives': [reference]}]
    metrics = {}
    if count is not None:
        metrics['stable_complete_record_count'] = count
    if fields is not None and expected and check_fields:
        metrics['stable_records'] = [{'fields': fields, 'valid': True, 'errors': []}]
    return {'id': 'structured-link-' + identifier, 'root_cause': '4409476573',
            'category': 'structured-extraction', 'failure_mode': 'false-negative' if expected else 'false-positive',
            'case': {'url': 'https://example.test/', 'acceptance_contract': contract}, 'artifact': source,
            'metadata': {'answer_format': representation}, 'ok': True, 'expected_task_pass': expected,
            **({'expected_metrics': metrics} if metrics else {})}


def controls():
    for schema in ['product', 'catalog_product', 'quote', 'earthquake', 'api_operation', 'review', 'name_description']:
        for identifier, markup, word in WORDS:
            source, _ = record(schema, markup)
            reference, fields = record(schema, word)
            yield control(schema + '-' + identifier, schema, source, True, fields=fields, reference=reference)
        source, _ = record(schema, '[read](/details)able')
        wrong_reference, wrong_fields = record(schema, 'read able')
        # Name/description contracts explicitly ignore scalar whitespace.
        yield control(schema + '-space-equivalence', schema, source, schema == 'name_description',
                      fields=wrong_fields, reference=wrong_reference, check_fields=False)
    for identifier, source, rendered in [
        ('suffix', '[New](/family)idae', 'Newidae'),
        ('prefix', 'New[idae](/family)', 'Newidae'),
        ('whole', '[Newidae](/family)', 'Newidae'),
    ]:
        yield control('taxonomy-' + identifier, 'taxonomy_family', source, True,
                      fields={'family': rendered}, reference=rendered)
    for representation, source in [
        ('html', '<article><p><a href="/details">read</a>able</p><p>$10.00</p></article>'),
        ('plain_text', '[read](/details)able\n$10.00'),
    ]:
        yield control('product-' + representation, 'product', source, representation == 'html',
                      fields={'name': 'readable', 'price': '$10.00'}, reference='readable\n$10.00', representation=representation)
    for identifier, source, word in [
        ('bullet-suffix', '- [read](/details)able\nUseful device', 'readable'),
        ('bullet-inline-suffix', '- [read](/details)able Useful device', 'readable'),
        ('bullet-hyphen', '- [reader](/details)-friendly\nUseful device', 'reader-friendly'),
        ('bullet-prefix', '- pre[fix](/details)\nUseful device', 'prefix'),
    ]:
        yield control(identifier, 'name_description', source, True,
                      fields={'name': word, 'description': 'Useful device'}, reference=word + '\nUseful device')
    yield control('compact-path-summary', 'api_operation', 'GET[/items](/docs)List current items', True,
                  fields={'method': 'GET', 'path': '/items', 'summary': 'List current items'},
                  reference='GET /items\nList current items')
    yield control('numeric-adjacent-labels', 'product', 'Coat\n$[1](/first)[0](/second).00', True,
                  fields={'name': 'Coat', 'price': '$10.00'}, reference='Coat\n$10.00')
    yield control('product-card-boundaries', 'product', '[Alpha\n$10](/a)\n[Beta\n$20](/b)', True, minimum=2, count=2)
    yield control('product-split-card', 'product', '[Alpha\nExtra](/a)\n[$10\nExtra](/b)', False, count=0)
    yield control('earthquake-card-boundaries', 'earthquake',
                  '[4.1\nPacific coast\n2026-01-01T12:00:00Z\n12 km](/event)', True, count=1)
    yield control('earthquake-split-card', 'earthquake',
                  '[4.1\nPacific coast](/a)\n[2026-01-01T12:00:00Z\n12 km](/b)', False, count=0)


CONTROLS = list(controls())


@pytest.mark.parametrize('row', CONTROLS, ids=[row['id'] for row in CONTROLS])
def test_structured_link_adjacency_through_public_scorer(row):
    metrics = score_attempt(row['case'], row['artifact'].encode(), ok=True, metadata=row['metadata'])
    assert task_passed(True, metrics, 'body') is row['expected_task_pass']
    for key, expected in row.get('expected_metrics', {}).items():
        assert metrics[key] == expected
