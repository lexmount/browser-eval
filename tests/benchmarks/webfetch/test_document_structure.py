"""Section titles require local returned body, not navigation or a word bag."""
from copy import deepcopy

import pytest

from lexbench_webfetch.document_structure import evaluate_document_structure, validate_document_structure
from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt


def contract():
    return {'version': 1, 'minimum_heading_coverage': .8, 'minimum_body_chars': 50,
            'headings': [{'id': str(i), 'text': name, 'level': 2, 'body_required': True}
                         for i, name in enumerate(['Install', 'Usage', 'Errors', 'Examples', 'Reference'])]}


def body(count=5):
    return '\n\n'.join('## '+h['text']+'\nA useful paragraph describes this section.'
                       for h in contract()['headings'][:count])


@pytest.mark.parametrize('count,passed', [(5, True), (4, True), (3, False)])
def test_section_coverage(count, passed):
    assert evaluate_document_structure(body(count), contract())['document_structure_pass'] is passed


@pytest.mark.parametrize('text', [
    'Install\nUsage\nErrors\nExamples\nReference\n'+'Unrelated filler. '*20,
    '\n'.join(f'[{h["text"]}](#target)' for h in contract()['headings'])+'\n'+'Filler. '*30,
    'This sentence mentions Install, Usage, Errors, Examples and Reference. '*10,
    'Reference Examples Errors Usage Install\n'+'Filler. '*30,
    '```\n'+body()+'\n```',
])
def test_toc_sentence_word_bag_and_code_are_not_sections(text):
    assert not evaluate_document_structure(text, contract())['document_structure_pass']


@pytest.mark.parametrize('tag', ['nav', 'header', 'footer', 'aside'])
def test_html_navigation_cannot_supply_headings(tag):
    nav=''.join('<h2>'+h['text']+'</h2><p>Navigation copy</p>' for h in contract()['headings'])
    text=f'<{tag}>{nav}</{tag}><main><p>'+('Other text. '*30)+'</p></main>'
    assert not evaluate_document_structure(text, contract(), html_document=True)['document_structure_pass']


def test_html_paragraphs_containing_title_words_are_not_headings():
    text='<main>'+''.join('<p>'+h['text']+'</p><p>Substantial content.</p>' for h in contract()['headings'])+'</main>'
    assert not evaluate_document_structure(text, contract(), html_document=True)['document_structure_pass']


def test_html_real_heading_elements_and_nested_inline_text():
    text='<main>'+''.join('<h2><span>'+h['text']+'</span></h2><p>Substantial content.</p>' for h in contract()['headings'])+'</main>'
    assert evaluate_document_structure(text, contract(), html_document=True)['document_structure_pass']


def test_html_comments_cannot_supply_section_body():
    text='<main>'+''.join('<h2>'+h['text']+'</h2><!-- renderer state -->'
                         for h in contract()['headings'])+'</main>'
    measured=evaluate_document_structure(text,contract(),html_document=True)
    assert measured['document_body_chars'] == 0
    assert not measured['document_structure_pass']


def test_visually_present_aria_hidden_prose_supplies_section_body():
    text='<main>'+''.join('<h2>'+h['text']+'</h2><p aria-hidden="true">'
                         'Visible paragraph describing the section.</p>'
                         for h in contract()['headings'])+'</main>'
    assert evaluate_document_structure(text,contract(),html_document=True)['document_structure_pass']


@pytest.mark.parametrize('style', ['display:none', 'display: none !important',
                                  'visibility:hidden', 'opacity:0'])
def test_explicit_css_hidden_prose_cannot_supply_section_body(style):
    text='<main>'+''.join('<h2>'+h['text']+'</h2><p style="'+style+'">'
                         'Hidden paragraph describing the section.</p>'
                         for h in contract()['headings'])+'</main>'
    measured=evaluate_document_structure(text,contract(),html_document=True)
    assert measured['document_body_chars'] == 0
    assert not measured['document_structure_pass']


def test_changed_html_heading_depth_cannot_let_siblings_borrow_one_paragraph():
    text='<main>'+''.join(f'<h{i+1}>{h["text"]}</h{i+1}>'
                         for i,h in enumerate(contract()['headings']))+'<p>'+('Filler. '*30)+'</p></main>'
    assert not evaluate_document_structure(text,contract(),html_document=True)['document_structure_pass']


def test_source_declared_empty_heading_is_not_fabricated_prose():
    c=contract()
    c['headings'][-1]['body_required']=False
    assert evaluate_document_structure(body(3)+'\n## Reference', c)['document_structure_pass']
    assert not evaluate_document_structure(body(3)+'\n## Reference', contract())['document_structure_pass']


def test_multiline_heading_preserves_order_and_needs_body():
    c=contract()
    c['headings'][0]['text']='Install the client'
    text=body().replace('## Install', 'Install\nthe\nclient')
    assert evaluate_document_structure(text,c)['document_structure_pass']
    c['minimum_heading_coverage']=1
    assert not evaluate_document_structure(text.replace('Install\nthe\nclient','client\nthe\nInstall'),c)['document_structure_pass']


def test_child_prose_supports_parent_heading():
    c=contract()
    c['headings'][1]['level']=3
    text=body().replace('## Install\nA useful paragraph describes this section.', '## Install')
    assert evaluate_document_structure(text,c)['document_structure_pass']


def test_structure_is_optional_and_final_task_predicate_enforces_it():
    case={'url':'https://example.test/manual','expected_any':[],
          'acceptance_contract': {'version':1,'schema':'body_text','minimum_records':0,
                                  'stable_text_anchors':[{'id':'title','alternatives':['Install']}]}}
    text='Install\nUsage\nErrors\nExamples\nReference\n'+'Filler. '*20
    assert task_passed(True,score_attempt(case,text.encode(),ok=True))
    case['document_structure']=contract()
    m=score_attempt(case,text.encode(),ok=True)
    assert m['stable_task_pass']
    assert not m['content_pass']
    assert m['content_decision_basis'].endswith('_and_document_structure')
    assert not task_passed(True,m)
    assert task_passed(True,score_attempt(case,body().encode(),ok=True))


@pytest.mark.parametrize('field,value', [('minimum_heading_coverage',True),('minimum_heading_coverage',float('nan')),
    ('minimum_heading_coverage',0),('minimum_heading_coverage',1.1),('minimum_body_chars',False),
    ('minimum_body_chars',0),('headings',[]),('version',True),('unknown',1)])
def test_invalid_contracts_fail_closed(field,value):
    c=contract();c[field]=value
    with pytest.raises(ValueError):validate_document_structure(c)


def test_all_empty_or_duplicate_headings_are_invalid():
    for c in [contract(),deepcopy(contract())]:
        if c['headings'][0]['body_required']:
            for h in c['headings']:h['body_required']=False
        with pytest.raises(ValueError):validate_document_structure(c)
    c=contract();c['headings'].append(deepcopy(c['headings'][0]))
    with pytest.raises(ValueError):validate_document_structure(c)


def test_title_facts_and_total_length_do_not_replace_section_body_checks():
    headings = contract()['headings']
    case = {'id': 'section-counterexample', 'url': 'https://example.test/manual',
            'min_content_chars': 100, 'fact_coverage_threshold': .8,
            'required_facts': [{'id': h['id'], 'alternatives': [h['text']]}
                               for h in headings]}
    toc = '\n'.join(f'[{h["text"]}](#section-{h["id"]})' for h in headings)
    answer = toc + '\n\n' + 'Unrelated footer text. ' * 30
    assert task_passed(True, score_attempt(case, answer.encode(), ok=True))
    case['document_structure'] = contract()
    assert not task_passed(True, score_attempt(case, answer.encode(), ok=True))
    assert task_passed(True, score_attempt(case, body().encode(), ok=True))


def test_omitted_heading_defaults_preserve_verdicts_and_explicit_exceptions():
    compact = {'version': 1, 'minimum_heading_coverage': 1,
               'minimum_body_chars': 20, 'headings': [
                   {'text': 'Usage', 'level': 2},
                   {'id': 'references', 'text': 'References', 'level': 2,
                    'body_required': False},
               ]}
    explicit = deepcopy(compact)
    explicit['headings'][0].update(id='section-1', body_required=True)
    for answer, passed in [
        ('Usage\nA useful description of the feature.\nReferences', True),
        ('Usage\nReferences', False),
        ('A useful description of the feature.\nReferences', False),
    ]:
        assert evaluate_document_structure(answer, compact) == evaluate_document_structure(answer, explicit)
        assert evaluate_document_structure(answer, compact)['document_structure_pass'] is passed
    compact['headings'][1]['id'] = 'section-1'
    with pytest.raises(ValueError, match='unique IDs'):
        validate_document_structure(compact)


def test_numbered_toc_children_cannot_replace_missing_chapter_body():
    c = {'version': 1, 'minimum_heading_coverage': 1, 'minimum_body_chars': 1,
         'headings': [{'text': '1. Methods', 'level': 1},
                      {'text': '2. Context', 'level': 1}]}
    toc = '1. Methods\n1.1. Overview\n1.2. Definitions\n2. Context\n2.1. Fields\n'
    methods = '1. Methods\n1.1. Overview\nRequests use these methods.\n'
    context = '2. Context\n2.1. Fields\nFields describe the request context.\n'
    assert evaluate_document_structure(toc + methods + context, c)['document_structure_pass']
    assert not evaluate_document_structure(toc + context, c)['document_structure_pass']
    assert not evaluate_document_structure(toc, c)['document_structure_pass']


def test_numbered_list_remains_body_for_unnumbered_section():
    c = {'version': 1, 'minimum_heading_coverage': 1, 'minimum_body_chars': 1,
         'headings': [{'text': 'Steps', 'level': 1}]}
    assert evaluate_document_structure('Steps\n1.1. Run the command.', c)['document_structure_pass']


def test_wrapped_numbered_toc_and_unrequired_sibling_are_not_body():
    c = {'version': 1, 'minimum_heading_coverage': 1, 'minimum_body_chars': 1,
         'headings': [{'text': '1. Methods', 'level': 1}]}
    toc = '  1. Methods\n    1.1.  A long heading\n        continued here\n  2. References\nA reference entry.\n'
    assert not evaluate_document_structure(toc, c)['document_structure_pass']
    text = toc + '\n1. Methods\n\n1.1. A long heading\n     continued here\n\n   An actual paragraph.\n'
    assert evaluate_document_structure(text, c)['document_structure_pass']


@pytest.mark.parametrize('minimum', [0, True, 1.5, '4'])
def test_invalid_local_section_floor(minimum):
    c = contract()
    c['minimum_section_chars'] = minimum
    with pytest.raises(ValueError, match='section character floor'):
        validate_document_structure(c)


def test_local_section_floor_cannot_borrow_other_section_or_punctuation():
    c = {'version': 1, 'minimum_heading_coverage': 1, 'minimum_body_chars': 1,
         'minimum_section_chars': 4,
         'headings': [{'text': 'Messages', 'level': 2}, {'text': 'Meetings', 'level': 2}]}
    for first, passed in [('x!!!!', False), ('一二三', False), ('一二三四', True)]:
        text = '## Messages\n' + first + '\n## Meetings\nA complete meeting description.'
        assert evaluate_document_structure(text, c)['document_structure_pass'] is passed


@pytest.mark.parametrize('wrapper', [
    lambda text: '\n'.join('    ' + line for line in text.splitlines()),
    lambda text: '\n'.join('\t' + line for line in text.splitlines()),
    lambda text: '````\n```\n' + text + '\n````',
    lambda text: '~~~\n~~~ example\n' + text + '\n~~~',
])
def test_code_examples_cannot_supply_section_headings(wrapper):
    assert not evaluate_document_structure(wrapper(body()), contract())['document_structure_pass']
    assert evaluate_document_structure(wrapper(body()) + '\n\n' + body(), contract())['document_structure_pass']


@pytest.mark.parametrize('owner', ['article', 'main'])
def test_article_header_keeps_local_heading(owner):
    c = {'version': 1, 'minimum_heading_coverage': 1, 'minimum_body_chars': 1,
         'headings': [{'text': 'Usage', 'level': 1}]}
    source = f'<{owner}><header><h1>Usage</h1></header><p>A substantive description.</p></{owner}>'
    assert evaluate_document_structure(source, c, html_document=True)['document_structure_pass']
    for tag in ['nav', 'header role="navigation"', 'header class="toc"']:
        excluded = source.replace('<header>', '<' + tag + '>').replace('</header>', '</' + tag.split()[0] + '>')
        assert not evaluate_document_structure(excluded, c, html_document=True)['document_structure_pass']


def test_tab_indented_numbered_heading_is_code():
    c = {'version': 1, 'minimum_heading_coverage': 1, 'minimum_body_chars': 1,
         'headings': [{'text': '1. Methods', 'level': 1}]}
    assert not evaluate_document_structure('\t1. Methods\n\nA substantive paragraph.', c)['document_structure_pass']
    assert evaluate_document_structure('1. Methods\n\nA substantive paragraph.', c)['document_structure_pass']


@pytest.mark.parametrize('underline', ['---', '==='])
def test_setext_boundary_prevents_borrowing_next_section(underline):
    c = {'version': 1, 'minimum_heading_coverage': 1, 'minimum_body_chars': 1,
         'headings': [{'text': 'Usage', 'level': 1 if underline[0] == '=' else 2}]}
    text = f'Usage\n{underline}\nOther\n{underline}\nDescription belonging only to Other.'
    assert not evaluate_document_structure(text, c)['document_structure_pass']
    assert evaluate_document_structure(text.replace('Other\n', 'Usage description.\n\nOther\n'), c)['document_structure_pass']


@pytest.mark.parametrize('title', ['Other section', 'Other\nsection', 'Other\nlong\nsection'])
def test_multiline_setext_heading_is_not_previous_section_body(title):
    c = {'version': 1, 'minimum_heading_coverage': 1, 'minimum_body_chars': 1,
         'headings': [{'text': 'Usage', 'level': 2}]}
    source = f'Usage\n---\n\n{title}\n---\nOther description.'
    assert not evaluate_document_structure(source, c)['document_structure_pass']
    assert evaluate_document_structure(source.replace('Usage\n---\n', 'Usage\n---\nUsage description.\n'), c)['document_structure_pass']


@pytest.mark.parametrize('separator,passed', [('\n', True), ('\n\n', False), ('\n  \n', False), ('\n```\n```\n', False), ('\n~~~\n~~~\n', False)])
def test_wrapped_heading_does_not_cross_paragraphs(separator, passed):
    c = {'version': 1, 'minimum_heading_coverage': 1, 'minimum_body_chars': 1,
         'headings': [{'text': 'Getting started', 'level': 2}]}
    text = 'Getting' + separator + 'started\n\nA substantive description.'
    assert evaluate_document_structure(text, c)['document_structure_pass'] is passed


@pytest.mark.parametrize('title,passed', [
    ('Installation', True), ('[Installation](https://example.com/install)', True),
    ('[Other](https://example.com/Installation)', False)])
def test_linked_heading_uses_visible_label(title, passed):
    c = {'version': 1, 'minimum_heading_coverage': 1, 'minimum_body_chars': 1,
         'headings': [{'text': 'Installation', 'level': 2}]}
    assert evaluate_document_structure('## ' + title + '\nA substantive description.', c)['document_structure_pass'] is passed
