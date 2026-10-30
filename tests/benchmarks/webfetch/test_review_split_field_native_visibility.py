"""Empty field labels retain ownership; HTML visibility precedes literal matching."""
import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt
from tests.benchmarks.webfetch.test_review_owned_fields_exclusions import composite_case


def field_case(kind, *, second=False):
    specs=[{'id':'author','kind':'text','aliases':['Author']}]
    if second:
        specs.append({'id':'owner','kind':'text','aliases':['Owner']})
    else:
        specs[0]['pattern'] = r'Alice'
    group={'id':'items','kind':kind,'required_fields':specs}
    if kind=='named_link':
        group.update(hosts=['example.test'],entry_path=r'/item/1',minimum_records=1)
    return composite_case(group)


def score(case, content, representation):
    metadata={'answer_format':representation} if representation else None
    metrics=score_attempt(case, content.encode(),ok=True,metadata=metadata)
    return task_passed(True,metrics,'body')


def content(kind, body, representation):
    if representation=='html':
        return ('<article><a href="/item/1">Example</a>' if kind=='named_link' else '<article>') + '<p>'+body+'</p></article>'
    prefix = ('Example\nhttps://example.test/item/1\n' if representation == 'plain_text' else '[Example](/item/1)\n') if kind == 'named_link' else ''
    return prefix + body


@pytest.mark.parametrize('kind',['field_report','named_link'])
@pytest.mark.parametrize('representation',[None,'markdown','html','plain_text'])
@pytest.mark.parametrize('label',['Author', 'Author:', 'Author：', 'Author |', 'Author -', 'Author –', 'Author —', 'Author (primary):'])
def test_supported_empty_label_punctuation_owns_following_value(kind, representation, label):
    assert score(field_case(kind),content(kind,label+'\nAlice',representation),representation)


@pytest.mark.parametrize('kind',['field_report','named_link'])
@pytest.mark.parametrize('representation',[None,'markdown','html','plain_text'])
@pytest.mark.parametrize('complete',[False,True])
def test_split_labels_do_not_borrow_the_next_declared_field(kind, representation, complete):
    body='Author:\n'+('Alice\n' if complete else '')+'Owner:\nBob'
    assert score(field_case(kind,second=True),content(kind,body,representation),representation) is complete


@pytest.mark.parametrize('representation',[None,'html','markdown','plain_text'])
@pytest.mark.parametrize('hidden',[False,True])
def test_native_visibility_wins_over_literal_backticks(representation,hidden):
    html='<p>`<span'+(' hidden' if hidden else '')+'>Alpha</span>`</p>'
    case=composite_case({'id':'visible','kind':'literals','required_literals':['Alpha']})
    expected=not hidden if representation != 'plain_text' else True
    assert score(case,html,representation) is expected


@pytest.mark.parametrize('representation',[None,'html'])
@pytest.mark.parametrize('hidden',[False,True])
def test_field_report_does_not_read_hidden_text_between_html_backticks(representation,hidden):
    html='<p>Author: `<span'+(' hidden' if hidden else '')+'>Alice</span>`</p>'
    assert score(field_case('field_report'),html,representation) is (not hidden)


@pytest.mark.parametrize('representation',[None,'markdown','plain_text'])
def test_markdown_inline_html_example_remains_visible_literal_content(representation):
    case=composite_case({'id':'visible','kind':'literals','required_literals':['Alpha']})
    assert score(case,'`<span hidden>Alpha</span>`',representation)


@pytest.mark.parametrize('attribute',['hidden','style="display:none"','style="visibility:hidden"'])
@pytest.mark.parametrize('representation',[None,'html'])
def test_native_visibility_is_not_changed_by_markdown_fence_characters(attribute,representation):
    case=composite_case({'id':'visible','kind':'literals','required_literals':['Alpha']})
    assert not score(case,'<p>```<span '+attribute+'>Alpha</span>```</p>',representation)


@pytest.mark.parametrize('representation',[None,'markdown','plain_text'])
@pytest.mark.parametrize('hidden',[False,True])
def test_mixed_markdown_raw_html_block_obeys_native_visibility(representation,hidden):
    case=composite_case({'id':'visible','kind':'literals','required_literals':['Alpha']})
    text='Intro\n\n<p>`<span'+(' hidden' if hidden else '')+'>Alpha</span>`</p>'
    assert score(case,text,representation) is (not hidden or representation=='plain_text')


@pytest.mark.parametrize('kind',['field_report','named_link'])
@pytest.mark.parametrize('representation',[None,'markdown','plain_text'])
def test_existing_bare_normalized_alias_remains_supported(kind,representation):
    assert score(field_case(kind),content(kind,'Ａｕｔｈｏｒ\nAlice',representation),representation)


@pytest.mark.parametrize('wrapper',['```html\n{block}\n```','Intro\n\n    {block}'])
def test_html_blocks_inside_markdown_code_remain_literal(wrapper):
    case=composite_case({'id':'visible','kind':'literals','required_literals':['Alpha']})
    assert score(case,wrapper.format(block='<p>`<span hidden>Alpha</span>`</p>'),'markdown')


@pytest.mark.parametrize('tag',['div','section'])
@pytest.mark.parametrize('representation',[None,'markdown'])
def test_nested_native_blocks_do_not_end_the_outer_literal_boundary(tag,representation):
    case=composite_case({'id':'visible','kind':'literals','required_literals':['Alpha']})
    text='Intro\n\n<'+tag+'>\n<'+tag+'>Other</'+tag+'>\n`<span hidden>Alpha</span>`\n</'+tag+'>'
    assert not score(case,text,representation)


@pytest.mark.parametrize('representation',[None,'markdown'])
def test_markdown_after_exact_native_close_is_still_a_literal_code_example(representation):
    case=composite_case({'id':'visible','kind':'literals','required_literals':['Alpha']})
    text='Intro\n\n<p>Other</p> `<span hidden>Alpha</span>`'
    assert score(case,text,representation)


@pytest.mark.parametrize('representation',[None,'markdown'])
def test_quoted_native_closing_tag_cannot_end_html_ownership(representation):
    case=composite_case({'id':'visible','kind':'literals','required_literals':['Alpha']})
    text='Intro\n\n<p title="</p>">`<span hidden>Alpha</span>`</p>'
    assert not score(case,text,representation)
