"""Malformed angle syntax and paragraph gaps never manufacture resource links."""
import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.reference_quality import markdown_links
from lexbench_webfetch.scoring import score_attempt


def resource_case(path):
    return {'url':'https://example.test/catalog','task_kind':'resource','expected_resources':[
        {'id':'item','kind':'link','urls':['https://example.test'+path]}]}


INVALID=[
    ('escaped-only-angle',r'[Alpha](</item\>)','/item%5C'),
    ('angle-newline','[Alpha](</item\nnext>)','/itemnext'),
    ('nested-angle','[Alpha](</item<next>)','/item<next'),
    ('label-destination-paragraph','[Alpha]\n\n(/item)','/item'),
    ('destination-leading-paragraph','[Alpha](\n\n/item)','/item'),
    ('destination-paragraph','[Alpha](/it\n\nem)','/item'),
    ('title-leading-paragraph','[Alpha](/item\n\n "Title")','/item'),
    ('title-trailing-paragraph','[Alpha](/item "Title"\n\n)','/item'),
    ('parenthesized-title-paragraph','[Alpha](/item\n\n (Title))','/item'),
    ('whitespace-paragraph','[Alpha]\n \t\n(/item)','/item'),
    ('crlf-paragraph','[Alpha]\r\n\r\n(/item)','/item'),
]


@pytest.mark.parametrize('identifier,text,path',INVALID)
def test_invalid_inline_link_has_no_fields_or_resource_evidence(identifier,text,path):
    assert markdown_links(text)==[]
    metrics=score_attempt(resource_case(path),text.encode(),ok=True)
    assert not task_passed(True,metrics,'resource')


VALID=[
    ('native-label-paragraph','[Alpha\n\nBeta](/item)','/item','','Alpha\n\nBeta','/item'),
    ('ordinary','[Alpha](/item)','/item','','Alpha','/item'),
    ('label-wrap','[Alpha]\n(/item)','/item','','Alpha','/item'),
    ('destination-wrap','[Alpha](\n/item\n)','/item','','Alpha','/item'),
    ('quoted-title-wrap','[Alpha](/item\n "Title")','/item','Title','Alpha','/item'),
    ('parenthesized-title-wrap','[Alpha](/item\n (Title))','/item','Title','Alpha','/item'),
    ('visible-label-wrap','[Alpha\nBeta](/item)','/item','','Alpha\nBeta','/item'),
    ('escaped-backslash',r'[Alpha](</item\\>)','/item\\','','Alpha','/item%5C'),
    ('native-url-wrap','[Alpha](/it\nem)','/item','','Alpha','/item'),
    ('native-repeated-quote','[Alpha](/item "say "hello" title")','/item','say "hello" title','Alpha','/item'),
]


@pytest.mark.parametrize('identifier,text,destination,title,label,path',VALID)
def test_valid_single_line_wrapping_preserves_every_link_field(identifier,text,destination,title,label,path):
    links=markdown_links(text)
    assert len(links)==1
    assert (links[0].destination,links[0].title,links[0].label)==(destination,title,label)
    assert task_passed(True,score_attempt(resource_case(path),text.encode(),ok=True),'resource')


def test_invalid_angle_or_paragraph_link_does_not_erase_a_later_valid_link():
    text=r'[Bad](</item\>)'+'\n\n[Bad]\n\n(/item)\n\n[Good](/valid "Title")'
    assert [(link.destination,link.title,link.label) for link in markdown_links(text)]==[('/valid','Title','Good')]
    assert task_passed(True,score_attempt(resource_case('/valid'),text.encode(),ok=True),'resource')


def test_comment_owned_links_do_not_supply_fields_or_resources():
    text='<!-- [Hidden](/item "Title") -->'
    assert markdown_links(text)==[]
    assert not task_passed(True,score_attempt(resource_case('/item'),text.encode(),ok=True),'resource')


def test_comment_delimiters_cannot_close_the_visible_link_label():
    text='[Alpha <!-- ](/wrong) -->](/item "Title")'
    links=markdown_links(text)
    assert len(links)==1
    assert (links[0].destination,links[0].title,links[0].label)==('/item','Title','Alpha <!-- ](/wrong) -->')
    assert task_passed(True,score_attempt(resource_case('/item'),text.encode(),ok=True),'resource')
