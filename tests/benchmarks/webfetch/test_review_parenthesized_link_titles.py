"""One optional-title grammar owns bare and angle-wrapped link destinations."""
import re

import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.reference_quality import markdown_links, visible_markdown_links
from lexbench_webfetch.scoring import score_attempt
from tests.benchmarks.webfetch.test_review_envelope_literals import CONTRACT


TITLE_FORMS=[('', ''), ('"special proof"','special proof'), ("'special proof'",'special proof'), ('(special proof)','special proof')]


def named_case(path):
    return {'url':'https://example.test/','acceptance_contract':{'version':1,'schema':'named_link',
        'minimum_records':1,'hosts':['example.test'],'entry_path':re.escape(path)}}


@pytest.mark.parametrize('bracketed',[False,True])
@pytest.mark.parametrize('separator',[' ','\t','\n'])
@pytest.mark.parametrize('path',['/item','/item/(part)'])
@pytest.mark.parametrize('title,expected_title',TITLE_FORMS)
def test_title_matrix_preserves_destination_title_and_label(bracketed,separator,path,title,expected_title):
    url='https://example.test'+path
    destination='<'+url+'>' if bracketed else url
    text='[Guide]('+destination+separator+title+')'
    links=markdown_links(text)
    assert len(links)==1
    assert (links[0].destination,links[0].title,links[0].label)==(url,expected_title,'Guide')
    assert visible_markdown_links(text)=='Guide'
    assert task_passed(True,score_attempt(named_case(path),text.encode(),ok=True),'body')


@pytest.mark.parametrize('bracketed',[False,True])
@pytest.mark.parametrize('separator',[' ','\t','\n'])
@pytest.mark.parametrize('opener,closer',[('"','"'),("'","'"),('(',')')])
@pytest.mark.parametrize('matching',[False,True])
def test_title_binding_requires_an_independent_matching_visible_cell(bracketed,separator,opener,closer,matching):
    url='https://example.com/item/1'
    destination='<'+url+'>' if bracketed else url
    link='[Details]('+destination+separator+opener+'Alpha Details'+closer+')'
    text='| '+('Alpha' if matching else 'Other title')+' | '+link+' |'
    case={'url':'https://example.com','acceptance_contract':CONTRACT}
    assert task_passed(True,score_attempt(case,text.encode(),ok=True),'body') is (matching and separator != '\n')


@pytest.mark.parametrize('bracketed',[False,True])
@pytest.mark.parametrize('title,expected',[
    (r'(Alpha \(beta\))','Alpha (beta)'),
    (r'(Alpha \))','Alpha )'),
    ('(Alpha\nBeta)','Alpha\nBeta'),
    ('(Alpha\\\nBeta)','Alpha\\\nBeta'),
    ('"Alpha\\\nBeta"','Alpha\\\nBeta'),
    (r'"Alpha \"beta\""','Alpha "beta"'),
])
def test_title_payload_escapes_and_line_breaks(bracketed,title,expected):
    url='https://example.test/item'
    text='[Guide]('+('<'+url+'>' if bracketed else url)+' '+title+')'
    link=markdown_links(text)[0]
    assert (link.destination,link.title,link.label)==(url,expected,'Guide')


@pytest.mark.parametrize('bracketed',[False,True])
@pytest.mark.parametrize('title',['(Alpha (Beta))','(Alpha\n\nBeta)','"Alpha\n\nBeta"',"'Alpha\n \nBeta'",'(Alpha)junk','"Alpha',"'Alpha\"",'(Alpha'])
def test_malformed_title_cannot_become_a_destination_or_metadata(bracketed,title):
    url='https://example.test/item'
    text='[Guide]('+('<'+url+'>' if bracketed else url)+' '+title+')'
    assert markdown_links(text)==[]
    assert not task_passed(True,score_attempt(named_case('/item'),text.encode(),ok=True),'body')


@pytest.mark.parametrize('title',['',' "Native title"',' (Native title)'])
def test_native_wrapped_bare_destination_layout_remains_supported(title):
    text='[Guide](https://example.\ntest/it\nem'+title+')'
    link=markdown_links(text)[0]
    assert link.destination=='https://example.test/item'
    assert link.title==('Native title' if title else '')


@pytest.mark.parametrize('bracketed',[False,True])
def test_native_repeated_quote_extension_is_preserved(bracketed):
    url='https://example.test/item'
    text='[Guide]('+('<'+url+'>' if bracketed else url)+' "say "hello" title")'
    assert (markdown_links(text)[0].destination,markdown_links(text)[0].title)==(url,'say "hello" title')


def test_escaped_parentheses_without_a_separator_belong_to_the_url():
    text=r'[Guide](https://example.test/item/a\(part\))'
    assert (markdown_links(text)[0].destination,markdown_links(text)[0].title)==('https://example.test/item/a(part)','')
