"""Language labels are frozen task data, not scorer-wide site onboarding."""
from .article_contract_fixtures import post_labels
from copy import deepcopy
import pytest
from lexbench_webfetch.article_comments import validate_comment_contract, verify_article_comments
from lexbench_webfetch.article_posts import validate_post_contract, verify_article_posts

C = {'version': 2, 'minimum_comments': 1, 'record_selector': '.comment',
     'author_selector': '.author', 'body_selector': '.body', 'section_markers': ['Comments'],
     'ignored_labels': ['返信'], 'unavailable_notices': ['非表示のコメント']}


def test_new_language_labels_are_task_local_and_whole_label_only():
    def check(text, contract):
        return verify_article_comments('<div class="comment"><span class="author">Reader</span><p class="body">'+text+'</p></div>', contract)['article_comments_pass']
    assert not check('返信', C)
    assert not check('非表示のコメント', C)
    assert check('返信について説明します。', C)
    assert check('Real response.', C)
    other = {**C, 'ignored_labels': [], 'unavailable_notices': []}
    assert check('返信', other)
    assert not check('', other)


@pytest.mark.parametrize('bad', [None, 'Reply', [1], [''], ['Reply', 'Reply']])
def test_invalid_declared_labels_fail_closed(bad):
    with pytest.raises(ValueError):
        validate_comment_contract({**C, 'ignored_labels': bad})


def test_post_labels_are_not_global_or_substring_filters():
    contract = {**post_labels(), 'version': 2, 'minimum_posts': 1, 'hosts': ['example.test'], 'entry_path': r'/p/\d+',
                'html_record_selector': 'article', 'html_permalink_selector': 'a',
                'html_author_selector': 'b', 'html_body_selector': 'p',
                'ignored_labels': ['返信'], 'unavailable_notices': ['非表示']}
    def check(body):
        html = '<article><a href="/p/1">Post</a><b>Author</b><p>'+body+'</p></article>'
        return verify_article_posts(html, contract, url='https://example.test/feed')['article_posts_pass']
    assert not check('返信')
    assert not check('非表示')
    assert check('返信について説明します。')
    assert not check('')


@pytest.mark.parametrize('field', ['ignored_labels', 'unavailable_notices'])
def test_missing_comment_labels_are_rejected(field):
    contract = deepcopy(C)
    del contract[field]
    with pytest.raises(ValueError, match='missing task-declared'):
        validate_comment_contract(contract)


def test_normalized_duplicate_labels_are_rejected():
    with pytest.raises(ValueError, match='Duplicate'):
        validate_comment_contract({**C, 'ignored_labels': ['Log In', ' ＬＯＧ　　ＩＮ ']})
    validate_comment_contract({**C, 'ignored_labels': ['Log In', ' **ＬＯＧ　ＩＮ** ']})


def test_whitespace_width_and_literal_emphasis_have_distinct_roles():
    contract = {**C, 'ignored_labels': ['  ＬＯＧ　　ＩＮ  '], 'unavailable_notices': [' Not   Available ']}
    for text in ['log in', 'LOG    IN', '<strong>log in</strong>', ':not available:']:
        html = '<div class="comment"><span class="author">Reader</span><p class="body">'+text+'</p></div>'
        assert not verify_article_comments(html, contract)['article_comments_pass']
    for text in ['**log in**', '_LOG    IN_']:
        html = '<div class="comment"><span class="author">Reader</span><p class="body">'+text+'</p></div>'
        assert verify_article_comments(html, contract)['article_comments_pass']
    literal_contract = {**contract, 'ignored_labels': ['**ＬＯＧ　ＩＮ**']}
    for text, expected in [('**log in**', False), ('<strong>log in</strong>', True)]:
        html = '<div class="comment"><span class="author">Reader</span><p class="body">'+text+'</p></div>'
        assert verify_article_comments(html, literal_contract)['article_comments_pass'] is expected


def test_only_relevant_current_contracts_declare_labels():
    import json
    from lexbench_webfetch.article_dataset import DEFINITIONS
    rules = json.loads((DEFINITIONS / 'rules.json').read_text())['rules']
    comments = [r['comment_contract'] for r in rules if 'comment_contract' in r]
    posts = [r['post_contract'] for r in rules if 'post_contract' in r]
    assert (len(rules), len(comments), len(posts)) == (100, 20, 5)
    for contract in comments:
        validate_comment_contract(contract)
    for contract in posts:
        validate_post_contract(contract)
    assert not (DEFINITIONS / 'labels.json').exists()


def test_task_declared_non_author_labels_do_not_reject_actual_attribution():
    contract = {**post_labels(), 'version': 2, 'minimum_posts': 1,
                'hosts': ['example.test'], 'entry_path': r'/p/\d+',
                'html_record_selector': 'article', 'html_permalink_selector': 'a',
                'html_author_selector': 'b', 'html_body_selector': 'p',
                'non_author_labels': ['返信']}
    def check(text):
        return verify_article_posts(text, contract, url='https://example.test/feed')['article_posts_pass']
    assert not check('## [返信](/p/1)\nVisible post body.')
    assert check('## Real Author [返信](/p/1)\nVisible post body.')
    assert not check('## [2026-10-22](/p/1)\nVisible post body.')
    assert check('## [Reader](/p/1)\nVisible post body.')
    for field in ['ignored_labels', 'unavailable_notices', 'non_author_labels']:
        incomplete = deepcopy(contract)
        del incomplete[field]
        with pytest.raises(ValueError, match='missing task-declared'):
            validate_post_contract(incomplete)


def test_post_whole_labels_share_rule_and_observation_normalization():
    contract = {**post_labels(), 'version': 2, 'minimum_posts': 1,
                'hosts': ['example.test'], 'entry_path': r'/p/\d+',
                'html_record_selector': 'article', 'html_permalink_selector': 'a',
                'html_author_selector': 'b', 'html_body_selector': 'p',
                'ignored_labels': ['  ＬＯＧ　　ＩＮ  '],
                'unavailable_notices': [' Not   Available '],
                'non_author_labels': [' View   Post ']}
    def check(body, author='Reader'):
        return verify_article_posts('## ['+author+'](/p/1)\n'+body, contract,
                                    url='https://example.test/feed')['article_posts_pass']
    for text in ['**log in**', '_LOG    IN_', ':not available:']:
        assert not check(text)
    assert check('How to log in securely.')
    assert not check('Visible body.', 'ＶＩＥＷ　　ＰＯＳＴ')
    assert check('Visible body.', 'View Post researcher')


def test_comment_emphasis_normalization_preserves_distinct_source_ids():
    from lexbench_webfetch.article_comments import valid_comment_records
    contract = {**C, 'ignored_labels': ['reply']}
    assert valid_comment_records([{'author': 'Reader', 'text': '_reply_'}], contract) == []
    rows = [{'author': 'Reader', 'text': 'Useful observation'},
            {'author': 'Reader', 'text': '_Useful observation_'}]
    assert len(valid_comment_records(rows, contract)) == 1
    identified = [dict(row, source_id=str(i)) for i, row in enumerate(rows)]
    assert len(valid_comment_records(identified, contract)) == 2
    assert len(valid_comment_records([{'author': 'Reader', 'text': 'A useful reply.'}], contract)) == 1
