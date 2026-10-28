"""DOM field order and redirected component bases preserve declared ownership."""
import pytest

from lexbench_webfetch.evaluation import evaluate_response
from tests.benchmarks.webfetch.article_contract_fixtures import post_labels
from tests.benchmarks.webfetch.test_review_record_formats import record_task


@pytest.mark.parametrize('tag', ['article', 'div', 'li', 'tr'])
@pytest.mark.parametrize('before', [False, True])
def test_single_product_owner_allows_price_before_or_after_title(tag, before):
    price, title = '<span>$10.00</span>', '<a href="/item/1">Widget</a>'
    answer = f'<{tag}>' + (price + title if before else title + price) + f'</{tag}>'
    contract = record_task('linked_product', price_patterns=[r'\$\d+\.\d{2}'])
    result = evaluate_response(contract, answer.encode())
    assert result['passed']
    assert result['metrics']['stable_records'][0]['fields']['price'] == '$10.00'


def test_price_order_flexibility_does_not_merge_other_product_owners():
    contract = record_task('linked_product', minimum_records=2, price_patterns=[r'\$\d+\.\d{2}'])
    good = ('<div><article><span>$10.00</span><a href="/item/1">Widget</a></article>'
            '<article><a href="/item/2">Other</a><span>$20.00</span></article></div>')
    result = evaluate_response(contract, good.encode())
    assert result['passed']
    assert [(row['fields']['url'], row['fields']['price']) for row in result['metrics']['stable_records']] == [
        ('https://shop.example/item/1', '$10.00'), ('https://shop.example/item/2', '$20.00')]
    bad = ('<div><article><span>$10.00</span><a href="/item/1">Widget</a></article>'
           '<article><a href="/item/2">Other</a></article></div>')
    assert not evaluate_response(contract, bad.encode())['passed']


def post_task(host='new.example'):
    return {'id': 'redirected-posts', 'name': 'Article feed', 'url': 'https://old.example/feed',
            'category': 'article', 'task_kind': 'body', 'expected_any': [],
            'source_benchmark': {'mode': 'article_post_collection', 'post_contract': {
                **post_labels(), 'version': 2, 'hosts': [host], 'entry_path': r'/post/\d+',
                'html_record_selector': 'article', 'html_permalink_selector': 'a.time',
                'html_author_selector': '.author', 'html_body_selector': '.body', 'minimum_posts': 1}}}


POST = ('<article><a class="time" href="/post/1">Today</a><b class="author">Alice</b>'
        '<p class="body">Orchard workers inspected every tree and published the harvest schedule.</p></article>')


@pytest.mark.parametrize('final_url,passed', [
    ('https://new.example/feed', True),
    ('https://other.example/feed', False),
    ('javascript:untrusted', False),
    ('https://[bad]/feed', False),
    (None, False),
])
def test_required_post_component_uses_observed_validated_response_base(final_url, passed):
    contract = post_task()
    contract['expected_final_url'] = 'https://new.example/feed'
    result = evaluate_response(contract, POST.encode(), final_url=final_url)
    assert result['passed'] is passed
    assert result['metrics']['article_posts_pass'] is passed


def test_invalid_observed_base_retains_the_requested_document_base():
    assert evaluate_response(post_task('old.example'), POST.encode(), final_url='https://[bad]/feed')['passed']
