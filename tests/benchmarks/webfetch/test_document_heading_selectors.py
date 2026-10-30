import copy
import pytest
from lexbench_webfetch.document_structure import evaluate_document_structure,validate_document_structure

C={'version':1,'minimum_heading_coverage':1,'minimum_body_chars':10,'headings':[{'id':'a','text':'Usage','level':2,'body_required':True,'html_selector':'.heading-h2[data-node="true"]'}]}
HTML='<main><div class="heading-h2" data-node="true">Usage</div><p>Describes actual usage.</p></main>'

def test_evidenced_custom_html_and_plaintext_pass():
    assert evaluate_document_structure(HTML,C,html_document=True)['document_structure_pass']
    assert evaluate_document_structure('Usage\nDescribes actual usage.',C)['document_structure_pass']

@pytest.mark.parametrize('body',[HTML.replace('heading-h2','ordinary'),HTML.replace('>Usage<','>Sentence containing Usage<'),HTML.replace('<main>','<main><nav>').replace('</main>','</nav></main>'),HTML.replace('<p>Describes actual usage.</p>','')])
def test_nonheading_navigation_and_empty_body_fail(body):
    assert not evaluate_document_structure(body,C,html_document=True)['document_structure_pass']

def test_absent_selector_keeps_default_behavior():
    c=copy.deepcopy(C);del c['headings'][0]['html_selector']
    assert not evaluate_document_structure(HTML,c,html_document=True)['document_structure_pass']

@pytest.mark.parametrize('selector',[None,'',5,'['])
def test_invalid_selector_rejected(selector):
    c=copy.deepcopy(C);c['headings'][0]['html_selector']=selector
    with pytest.raises(ValueError):validate_document_structure(c)

@pytest.mark.parametrize('wrapper',[ '<div hidden>{}</div>', '<div style="display:none">{}</div>', '<details><summary>More</summary>{}</details>' ])
def test_hidden_and_closed_details_custom_titles_are_excluded(wrapper):
    inner='<div class="heading-h2" data-node="true">Usage</div><p>Describes actual usage.</p>'
    assert not evaluate_document_structure('<main>'+wrapper.format(inner)+'</main>',C,html_document=True)['document_structure_pass']

def test_native_heading_without_selector_still_passes():
    c=copy.deepcopy(C);del c['headings'][0]['html_selector']
    assert evaluate_document_structure('<main><h2>Usage</h2><p>Describes actual usage.</p></main>',c,html_document=True)['document_structure_pass']
