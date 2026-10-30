"""Catalog fields retain section ownership; literal source is not a rendered link."""
import hashlib

import pytest

from lexbench_webfetch.dynamic_lists import named_entry_source_pair_hash
from lexbench_webfetch.evaluation import evaluate_response
from lexbench_webfetch.stable_tasks import evaluate_stable_task


CATALOG = {'version': 1, 'schema': 'catalog_product', 'minimum_records': 1, 'maximum_records': 1}
CATALOG_CASES = [
    ('peer-heading', '## Alpha\n$10\n\n## Store reviews\nHelpful service\n3 reviews', False),
    ('enclosing-heading', '### Alpha\n$10\n\n## Store reviews\nHelpful service\n3 reviews', False),
    ('owned-subheading', '## Alpha\n$10\n\n### Details\nUseful device description\n3 reviews', True),
    ('complete-before-peer', '## Alpha\n$10\nUseful device description\n3 reviews\n\n## Store reviews\nHelpful service\n9 reviews', True),
    ('price-first-peer', '$10\n## Alpha\n\n## Store reviews\nHelpful service\n3 reviews', False),
    ('price-first-owned', '$10\n## Alpha\n\n### Details\nUseful device description\n3 reviews', True),
    ('html-peer', '<main><h2>Alpha</h2><p>$10</p><h2>Store reviews</h2><p>Helpful service</p><p>3 reviews</p></main>', False),
    ('html-owned', '<main><h2>Alpha</h2><p>$10</p><h3>Details</h3><p>Useful device description</p><p>3 reviews</p></main>', True),
    ('html-card-peer', '<article><h2>Alpha</h2><p>$10</p><h2>Store reviews</h2><p>Helpful service</p><p>3 reviews</p></article>', False),
    ('html-card-owned', '<article><h2>Alpha</h2><p>$10</p><h3>Details</h3><p>Useful device description</p><p>3 reviews</p></article>', True),
]


def _link_case(schema):
    contract = {'version': 1, 'schema': schema, 'minimum_records': 1,
                'hosts': ['example.test'], 'entry_path': r'/item/\d+', 'stable_text_anchors': []}
    if schema == 'linked_product':
        contract['price_patterns'] = [r'\$\d+']
    pair = named_entry_source_pair_hash('Alpha', 'https://example.test/item/1', contract)
    contract.update(source_record_pair_hashes=[pair],
                    source_record_pairs_sha256=hashlib.sha256(pair.encode()).hexdigest())
    return {'url': 'https://example.test/', 'acceptance_contract': contract}


LINK_CASES = []
for schema in ['named_link', 'linked_product']:
    suffix = '\n$10' if schema == 'linked_product' else ''
    for identifier, text in [
        ('relative-markdown', '[Alpha](/item/1)'),
        ('absolute-markdown', '[Alpha](https://example.test/item/1)'),
        ('relative-html', '<a href="/item/1">Alpha</a>'),
        ('absolute-html', '<a href="https://example.test/item/1">Alpha</a>'),
        ('literal-label', '**Alpha**\nhttps://example.test/item/1'),
    ]:
        LINK_CASES.append((schema + '-' + identifier, text + suffix, _link_case(schema),
                           {'answer_format': 'plain_text'}, False))
    LINK_CASES.append((schema + '-plain-row', 'Alpha\nhttps://example.test/item/1' + suffix,
                       _link_case(schema), {'answer_format': 'plain_text'}, True))
    LINK_CASES.append((schema + '-markdown-rendered', '[Alpha](/item/1)' + suffix,
                       _link_case(schema), {'answer_format': 'markdown'}, True))
LINK_CASES.append(('named-metadata', 'Useful introduction', _link_case('named_link'),
                   {'answer_format': 'plain_text', 'links': [{'text': 'Alpha', 'href': 'https://example.test/item/1'}]}, True))

NATIVE_IMAGE = ('        - [![Generic device](device.png)](/item/1)\n'
                '          [Generic device](/item/1)\n\nA clear device description.')
NATIVE_CASE = {'url': 'https://example.test/', 'acceptance_contract': {
    'version': 1, 'schema': 'body_text', 'minimum_records': 0, 'stable_text_anchors': [],
    'page_patterns': [r'(?m)^[ \t]*Generic device[ \t]*$']}}
NATIVE_CASES = [('native-auto', NATIVE_IMAGE, None, True),
                ('native-markdown', NATIVE_IMAGE, 'markdown', False),
                ('native-plain', NATIVE_IMAGE, 'plain_text', False),
                ('native-code', NATIVE_IMAGE.replace('A clear device description.', '    A clear device description.'), None, False),
                ('native-fenced', '```markdown\n' + NATIVE_IMAGE + '\n```', None, False)]


@pytest.mark.parametrize('identifier,text,passed', CATALOG_CASES)
def test_catalog_description_and_review_count_stay_in_the_product_section(identifier, text, passed):
    assert evaluate_stable_task(text, CATALOG)['stable_task_pass'] is passed
    assert evaluate_response({'url': 'https://example.test/', 'acceptance_contract': CATALOG}, text.encode())['passed'] is passed


@pytest.mark.parametrize('identifier,text,case,metadata,passed', LINK_CASES)
def test_linked_record_representation_and_source_hash_use_the_delivered_literal_name(identifier, text, case, metadata, passed):
    assert evaluate_response(case, text.encode(), metadata=metadata)['passed'] is passed


@pytest.mark.parametrize('identifier,text,representation,passed', NATIVE_CASES)
def test_stable_native_layout_inference_is_limited_to_undeclared_serializer_cards(identifier, text, representation, passed):
    assert evaluate_stable_task(text, NATIVE_CASE['acceptance_contract'], representation=representation)['stable_task_pass'] is passed
    metadata = None if representation is None else {'answer_format': representation}
    assert evaluate_response(NATIVE_CASE, text.encode(), metadata=metadata)['passed'] is passed


def scoring_controls():
    for identifier, text, passed in CATALOG_CASES:
        yield {'id': 'runtime14-' + identifier, 'category': 'structured-extraction',
               'failure_mode': 'false-negative' if passed else 'false-positive',
               'root_cause': 'catalog_description_and_reviews_cannot_cross_peer_or_enclosing_sections',
               'case': {'url': 'https://example.test/', 'acceptance_contract': CATALOG},
               'artifact': text, 'ok': True, 'expected_task_pass': passed}
    for identifier, text, case, metadata, passed in LINK_CASES:
        yield {'id': 'runtime14-' + identifier, 'category': 'answer-surface',
               'failure_mode': 'false-negative' if passed else 'false-positive',
               'root_cause': 'linked_record_representation_and_source_identity_use_literal_answer_fields',
               'case': case, 'artifact': text, 'metadata': metadata, 'ok': True, 'expected_task_pass': passed}
    for identifier, text, representation, passed in NATIVE_CASES:
        yield {'id': 'runtime14-' + identifier, 'category': 'format-normalization',
               'failure_mode': 'false-negative' if passed else 'false-positive',
               'root_cause': 'stable_native_serializer_layout_inference_is_not_explicit_markdown_parsing',
               'case': NATIVE_CASE, 'artifact': text, 'metadata': None if representation is None else {'answer_format': representation},
               'ok': True, 'expected_task_pass': passed}
