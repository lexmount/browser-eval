"""Standalone images bound cards independently of blank-line serialization."""
import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt

CASE = {'id': 'media-cards', 'url': 'https://example.test/', 'min_content_chars': 1,
        'composite_acceptance': {'version': 1, 'groups': [
            {'id': 'cards', 'kind': 'name_description', 'minimum_records': 2}]}}
ALPHA = '![](/alpha.png)\nAlpha Film\nFirst film synopsis\nAlpha Film'
BETA = '![](/beta.png)\nBeta Film\nSecond film synopsis\nBeta Film'


@pytest.mark.parametrize('separator', ['\n', '\n\n'])
@pytest.mark.parametrize('prefix', ['', 'Loading content\n'])
def test_media_card_ownership_is_independent_of_blank_lines(separator, prefix):
    answer = prefix + (ALPHA+'\n'+BETA).replace('\n', separator)
    metrics = score_attempt(CASE, answer.encode(), ok=True)
    group = metrics['composite_group_results'][0]
    assert task_passed(True, metrics)
    assert group['count'] == 2
    assert {row['fields']['name'] for row in group['records']} == {'Alpha Film', 'Beta Film'}
    assert {row['fields']['description'] for row in group['records']} == {'First film synopsis', 'Second film synopsis'}


@pytest.mark.parametrize('answer', [
    '![](/alpha.png)\nAlpha Film\n'+BETA,
    '![](/alpha.png)\nAlpha Film\nFirst film synopsis\n![](/beta.png)\nAlpha Film',
    '![](/alpha.png)\nAlpha Film\nFirst film synopsis\n# Footer\nAlpha Film',
    '![](/alpha.png)\nAlpha Film\nAlpha Film\nAlpha Film\n'+BETA,
])
def test_orphan_media_cards_do_not_borrow_descriptions_or_page_boundaries(answer):
    metrics = score_attempt(CASE, answer.encode(), ok=True)
    assert not task_passed(True, metrics)
    assert metrics['composite_group_results'][0]['count'] <= 1
