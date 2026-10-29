"""Parentheses inside angle-delimited destinations are URL payload, not syntax."""
import re

import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.reference_quality import markdown_links, visible_markdown_links
from lexbench_webfetch.scoring import score_attempt
from tests.benchmarks.webfetch.test_review_literal_pattern_surface import contract


PATHS=['/item/a(b','/item/a)b','/item/a(b)c','/item/a((b','/item/a))b']


def named_case(path):
    return {'url':'https://example.test/','acceptance_contract':{'version':1,'schema':'named_link',
        'minimum_records':1,'hosts':['example.test'],'entry_path':re.escape(path)}}


@pytest.mark.parametrize('path',PATHS)
@pytest.mark.parametrize('title',['', ' "Title (with parens)"', " 'Title ) ('"])
def test_angle_destination_preserves_parentheses_and_optional_title(path,title):
    destination='https://example.test'+path
    source='[Guide](<'+destination+'>'+title+')'
    links=markdown_links(source)
    assert len(links)==1
    assert links[0].destination==destination
    assert links[0].end==len(source)
    assert visible_markdown_links(source)=='Guide'
    metrics=score_attempt(named_case(path),source.encode(),ok=True)
    assert task_passed(True,metrics,'body')


@pytest.mark.parametrize('path',PATHS)
def test_angle_destination_is_removed_from_public_visible_body(path):
    source='[Guide](<https://example.test'+path+'>)'
    case={'url':'https://example.test/','acceptance_contract':contract('^Guide$')}
    assert task_passed(True,score_attempt(case,source.encode(),ok=True),'body')


@pytest.mark.parametrize('source',[
    '[Guide](https://example.test/item/a(b)',
    '[Guide](https://example.test/item/a)b)',
    '[Guide](<https://example.test/item/a(b)',
])
def test_malformed_or_partial_destinations_cannot_satisfy_full_identity(source):
    metrics=score_attempt(named_case('/item/a(b'),source.encode(),ok=True)
    assert not task_passed(True,metrics,'body')


@pytest.mark.parametrize('source',[
    '`[Guide](<https://example.test/item/a(b>)`',
    '```markdown\n[Guide](<https://example.test/item/a(b>)\n```',
    '    [Guide](<https://example.test/item/a(b>)',
])
def test_code_examples_still_keep_their_literal_destination(source):
    assert visible_markdown_links(source)==source
    assert not task_passed(True,score_attempt(named_case('/item/a(b'),source.encode(),ok=True),'body')


def test_escaped_angle_does_not_end_the_destination_early():
    source=r'[Guide](<https://example.test/item/a\>b(c>)'
    assert markdown_links(source)[0].destination=='https://example.test/item/a>b(c'
    assert visible_markdown_links(source)=='Guide'


def test_angle_destination_does_not_affect_a_following_balanced_link():
    source='[First](<https://example.test/item/a(b>) [Second](https://example.test/item/a(b)c)'
    assert [link.destination for link in markdown_links(source)]==[
        'https://example.test/item/a(b','https://example.test/item/a(b)c']
    assert visible_markdown_links(source)=='First Second'


@pytest.mark.parametrize('suffix',['garbage)', ' "unclosed)', '>extra)', ''])
def test_new_angle_support_does_not_admit_malformed_outer_suffix(suffix):
    source='[Guide](<https://example.test/item/a(b>'+suffix
    assert not task_passed(True,score_attempt(named_case('/item/a(b'),source.encode(),ok=True),'body')


def test_parenthesized_optional_title_keeps_angle_destination():
    source='[Guide](<https://example.test/item/a(b> (Optional title))'
    assert markdown_links(source)[0].destination=='https://example.test/item/a(b'
    assert task_passed(True,score_attempt(named_case('/item/a(b'),source.encode(),ok=True),'body')
