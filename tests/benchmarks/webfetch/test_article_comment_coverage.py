"""Static initial-comment coverage does not freeze changing discussion text."""
from .article_contract_fixtures import comment_labels
from copy import deepcopy

import pytest

from lexbench_webfetch.article_comments import validate_comment_contract, verify_article_comments
from lexbench_webfetch.article_qualification import qualify_article_comments

CONTRACT = {**comment_labels(), 'version': 2, 'minimum_comments': 2, 'record_selector': '.comment',
            'author_selector': '.author', 'body_selector': '.text',
            'section_markers': ['Comments', '评论'], 'id_attribute': 'data-id'}


def check(content, contract=CONTRACT):
    return verify_article_comments(content, contract)


def card(author, body, identity=''):
    return f'<div class="comment" data-id="{identity}"><span class="author">{author}</span><div class="text">{body}</div></div>'


@pytest.mark.parametrize('content', [
    card('New user', 'New comment', '1') + card('Another user', 'Another comment', '2'),
    '## Comments\n### New user\nNew comment\n### Another user\nAnother comment',
    '## Comments\n#### New user\nNew comment\n#### Another user\nAnother comment',
    '## Comments\n\nNew user:\nNew comment\n\nAnother user:\nAnother comment',
    '## Comments\n\nNew user: New comment\n\nAnother user: Another comment',
    'Comments\n\n[New user](https://example.test/u)\nNew comment\n\n**Another user**\nAnother comment',
    'Comments\nPingback: [First source](https://a.test)\nPingback: [Second source](https://b.test)',
    card('New user', 'New comment' + card('Another user', 'Another comment', '2'), '1'),
])
def test_static_coverage_accepts_changed_names_text_and_flat_or_nested_records(content):
    result = check(content)
    assert result['article_comments_pass']
    assert result['article_comment_count'] == 2
    assert result['article_comment_minimum'] == 2
    assert result['article_comments_mode'] == 'fixed_coverage'
    assert 'article_comments_reference_status' not in result


@pytest.mark.parametrize('content', [
    card('Only one', 'Text'),
    card('', 'Text') + card('Name', ''),
    '<div hidden>' + card('A', 'One') + card('B', 'Two') + '</div>',
    '<details><summary>Comments</summary>' + card('A', 'One') + card('B', 'Two') + '</details>',
    card('A', '<img alt="text" src="a.png">') + card('B', '<img src="b.png">'),
    '## Comments\n### Login\nSign in\n### Load more\nClick here',
    '## Comments\n### A\n\n### B\n',
    '## Comments\n```\n### A\nFake\n### B\nFake\n```',
    '## Comments\n### A\nOne\n## Related articles\n### B\nTwo',
    '## Comments\nJust a paragraph with no author.\nAnother paragraph.',
    card('A', 'One', 'same') + card('B', 'Two', 'same'),
    '## Comments\n### A\nOne\n### A\nOne',
    card('A', card('B', 'Two', 'child'), 'parent'),
])
def test_missing_hidden_control_unattributed_and_duplicate_records_fail(content):
    assert not check(content)['article_comments_pass']


def test_equal_comments_with_distinct_source_ids_are_separate_records():
    assert check(card('A', 'Same text', '1') + card('A', 'Same text', '2'))['article_comments_pass']


def test_more_records_than_minimum_pass_and_symbol_content_is_allowed():
    result = check(card('????', '👍', '1') + card('A', 'One', '2') + card('B', 'Two', '3'))
    assert result['article_comments_pass']
    assert result['article_comment_count'] == 3


@pytest.mark.parametrize('changes', [
    {**comment_labels(), 'minimum_comments': 0}, {**comment_labels(), 'minimum_comments': True}, {**comment_labels(), 'minimum_comments': 1.5},
    {'parent_record_selector': '.comment'}, {'max_reference_age_seconds': 900},
    {'mode': 'observed_absent'}, {'record_selector': '['}, {'section_markers': []},
])
def test_static_contract_rejects_invalid_floor_and_legacy_requirements(changes):
    with pytest.raises(ValueError):
        validate_comment_contract(dict(CONTRACT, **changes))


def test_authoring_qualification_requires_enough_nonempty_textual_records():
    reference = {'version': 1, 'case_id': 'case', 'url': 'https://example.test/',
                 'scope': 'initial_load_only', 'observation_complete': True,
                 'context_id': 'authoring', 'observed_on': '2026-10-19',
                 'source_observation_counts': [2],
                 'source_evidence': [{'path': 'capture.json', 'sha256': 'a' * 64, 'scope': 'complete_initial_page'}],
                 'records': [
                    {'id': '1', 'author': 'A', 'text': 'One', 'parent_id': None, 'complete_initial_text': True},
                    {'id': '2', 'author': 'B', 'text': 'Two', 'parent_id': '1', 'complete_initial_text': True}]}
    result = qualify_article_comments(CONTRACT, reference, case_id='case', url=reference['url'])
    assert result['status'] == 'qualified'
    assert result['valid_comment_count'] == 2
    changed = deepcopy(reference)
    changed['records'][1].update(text='', empty_body=True, image_only=True, source_media_count=1)
    result = qualify_article_comments(CONTRACT, changed, case_id='case', url=reference['url'])
    assert result['status'] == 'insufficient_comments'
    assert result['valid_comment_count'] == 1


@pytest.mark.parametrize('placeholder', ['[内容不可见]', '[deleted]', 'This comment has been hidden', 'This comment has been hidden (marked as Spam)'])
def test_unavailable_content_notice_is_not_comment_prose(placeholder):
    assert not check(card('A', placeholder, '1') + card('B', placeholder, '2'))['article_comments_pass']
    # A quoted notice inside an actual comment is not discarded.
    assert check(card('A', placeholder + ' I disagree with this.', '1') + card('B', 'This is my comment.', '2'))['article_comments_pass']


def test_links_and_colon_prose_inside_one_heading_comment_do_not_inflate_count():
    text = ('## Comments\n### Alice\nOne real comment\n\n'
            '[Documentation](https://example.test/docs)\nUseful text\n\n'
            'Example: this is still the same comment\n\n'
            'Note:\nMore of the same comment')
    result = check(text)
    assert result['article_comment_count'] == 1
    assert not result['article_comments_pass']


@pytest.mark.parametrize('stamp', ['2026-10-20 10:00', '2026-10-20', '10:20 AM', '3 hours ago', '3小时前', '刚刚'])
def test_author_and_timestamp_without_comment_text_does_not_pass(stamp):
    text = f'## Comments\n### Alice\n{stamp}\nReply\n### Bob\n{stamp}\nReply'
    assert check(text)['article_comment_count'] == 0
    with_text = text.replace('\nReply', '\nActual comment text.\nReply')
    assert check(with_text)['article_comments_pass']
    # An HTML-owned body is actual comment text, even when it is just a date.
    assert check(card('Alice', stamp, '1') + card('Bob', stamp, '2'))['article_comments_pass']



def test_plain_navigation_comment_label_does_not_make_article_captions_comments():
    text = ('Home\nComments\nArticle title\nFull article opening.\n\n'
            'Image source: Example magazine\nMore article prose.\n\n'
            'Photo: Example photographer\nStill article prose.\n\n'
            '▲ 图片来自: Example site\nArticle ending.')
    assert check(text)['article_comment_count'] == 0
    assert not check(text)['article_comments_pass']


@pytest.mark.parametrize('number,expected', [
    (5,6),(9,1),(14,3),(17,4),(23,8),(25,204),(27,1),(30,18),
    (32,20),(44,24),(47,3),(49,1),(52,4),(53,4),(68,11),(92,12),
    (93,16),(95,56),(98,40),(103,20),
])
def test_published_comment_floors_accept_boundary_and_reject_one_fewer(number, expected):
    import json
    from lexbench_webfetch.article_dataset import DEFINITIONS
    rules = json.loads((DEFINITIONS / 'rules.json').read_text())['rules']
    rule = next(row for row in rules if row['id'] == f'article-{number:03}')
    contract = rule['comment_contract']
    assert contract['minimum_comments'] == expected
    records = [f'### Current reader {i}\nCurrent comment prose {i}.' for i in range(expected)]
    marker = '## ' + contract['section_markers'][0] + '\n'
    assert check(marker + '\n\n'.join(records), contract)['article_comments_pass']
    assert not check(marker + '\n\n'.join(records[:-1]), contract)['article_comments_pass']
