"""Reviewed text boundaries prevent unrelated page records from counting."""
from .article_contract_fixtures import comment_labels
from copy import deepcopy

import pytest

from lexbench_webfetch.article_comments import validate_comment_contract, verify_article_comments

CONTRACT = {**comment_labels(),
    'version': 2, 'minimum_comments': 2, 'record_selector': '.comment',
    'author_selector': '.author', 'body_selector': '.body',
    'section_markers': ['Comments', 'Post your reply'],
    'text_scope': {'body_end': 'The article ends right here.',
                   'end_markers': ['Recommended articles', 'About this site']},
}


def check(content, contract=CONTRACT):
    return verify_article_comments(content, contract)


COMMENTS = '\n\n**Alice**\nFirst actual reply.\n\n**Bob**\nSecond actual reply.'


@pytest.mark.parametrize('ending', [
    'The article ends right here.',
    'The article ends\nright here.',
    'The **article** ends [right here](https://example.test/end).',
])
def test_body_end_accepts_replies_without_input_placeholder(ending):
    assert check(ending + COMMENTS)['article_comment_count'] == 2


@pytest.mark.parametrize('heading', ['Comments', '## Comments', 'Post your reply'])
def test_optional_marker_after_body_ending_still_works(heading):
    assert check('The article ends right here.\n' + heading + COMMENTS)['article_comments_pass']


def test_navigation_and_article_captions_cannot_fill_comment_floor():
    content = ('Comments\n\n**Photo author**\nArticle image caption.\n\n'
               '**Other author**\nArticle quotation.\nThe article ends right here.')
    assert check(content)['article_comment_count'] == 0


@pytest.mark.parametrize('footer', ['Recommended articles', '## Recommended articles', 'About this site'])
def test_plain_or_heading_footer_does_not_supply_fake_comments(footer):
    content = ('The article ends right here.\nComments\n\n**Alice**\nOne actual reply.\n\n'
               + footer + '\n\n[News author](https://example.test/other)\nOther article summary.')
    result = check(content)
    assert result['article_comment_count'] == 1
    assert not result['article_comments_pass']


def test_missing_body_ending_is_not_an_extra_exact_text_requirement():
    assert check('Different or truncated article ending.\n## Comments' + COMMENTS)['article_comments_pass']


def test_missing_body_and_missing_heading_fail_closed():
    assert not check('Home\nComments' + COMMENTS)['article_comments_pass']


def test_comment_form_below_records_does_not_discard_earlier_comments():
    assert check('The article ends right here.' + COMMENTS + '\nPost your reply')['article_comments_pass']


def test_explicit_comment_heading_stops_at_same_level_even_without_known_footer():
    content = ('The article ends right here.\n## Comments\n### Alice\nOne reply.\n'
               '## Unrelated section\n### News author\nOther article summary.')
    assert check(content)['article_comment_count'] == 1


def test_colon_records_supported_after_reviewed_body_ending_without_ui_marker():
    content = 'The article ends right here.\n\nAlice: First actual reply.\n\nBob: Second actual reply.'
    assert check(content)['article_comments_pass']


def test_known_footer_inside_fenced_comment_is_literal_prose():
    content = ('The article ends right here.\n## Comments\n### Alice\n'
               '```\nRecommended articles\n```\n### Bob\nSecond reply.')
    assert check(content)['article_comments_pass']


@pytest.mark.parametrize('scope', [None, {}, {'body_end': 'end'},
    {'body_end': '', 'end_markers': []}, {'body_end': 'end', 'end_markers': ['']},
    {'body_end': 'end', 'end_markers': 'Footer'},
    {'body_end': 'end', 'end_markers': [], 'extra': True}])
def test_scope_contract_rejects_malformed_boundaries(scope):
    contract = deepcopy(CONTRACT)
    contract['text_scope'] = scope
    with pytest.raises(ValueError):
        validate_comment_contract(contract)


def test_metadata_between_body_and_comment_heading_preserves_heading_stop():
    contract = deepcopy(CONTRACT)
    contract['text_scope']['end_markers'] = []
    content = ('The article ends right here.\n12 likes\n## Comments\n'
               '### Alice\nOne real reply.\n## Related\n### Other author\nOther summary.')
    result = check(content, contract)
    assert result['article_comment_count'] == 1
    assert not result['article_comments_pass']


@pytest.mark.parametrize('ending_present', [True, False])
@pytest.mark.parametrize('footer', ['Post your reply', '## Post your reply'])
def test_reviewed_end_marker_wins_over_overlapping_start_marker(ending_present, footer):
    contract = deepcopy(CONTRACT)
    contract['text_scope']['end_markers'].append('Post your reply')
    prefix = 'The article ends right here.\n' if ending_present else ''
    result = check(prefix + footer + COMMENTS, contract)
    assert result['article_comment_count'] == 0
    assert not result['article_comments_pass']


def test_earlier_comments_still_count_when_later_form_is_also_start_marker():
    contract = deepcopy(CONTRACT)
    contract['text_scope']['end_markers'].append('Post your reply')
    result = check('The article ends right here.' + COMMENTS + '\nPost your reply' + COMMENTS, contract)
    assert result['article_comments_pass']
    assert result['article_comment_count'] == 2


@pytest.mark.parametrize('ending,full_body', [
    ('t really coding - I just see stuff, and it mostly works.',
     "A longer post. It's not really coding - I just see stuff, and it mostly works."),
    ('t really coding - I just see stuff, and it mostly wor',
     "A longer post. It's not really coding - I just see stuff, and it mostly works."),
    ('t really coding - I just see stuff, and it mostly works.',
     "A longer post. It's not **really** coding - I just see\nstuff, and it mostly works."),
])
def test_character_sliced_ending_matches_inside_full_body_tokens(ending, full_body):
    contract = deepcopy(CONTRACT)
    contract['text_scope']['body_end'] = ending
    result = check(full_body + COMMENTS, contract)
    assert result['article_comments_pass']
    assert result['article_comment_count'] == 2
