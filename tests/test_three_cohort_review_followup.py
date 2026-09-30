"""Scorer controls added after the historical dynamic-audit test inventory froze."""

from harness.web_fetch.stable_tasks import evaluate_stable_task
from harness.web_fetch.scoring import score_attempt
from unittest.mock import patch
import pytest


def test_title_with_angle_bracket_does_not_expose_hidden_html_anchor():
    rules = {
        "version": 1,
        "schema": "body_text",
        "minimum_records": 0,
        "stable_text_anchors": [{"id": "anchor", "alternatives": ["Secret Anchor"]}],
    }
    hidden = ("A < B\n<html><body><script>Secret Anchor</script>"
              "<p>Visible content</p></body></html>")
    visible = hidden.replace("<script>Secret Anchor</script>", "<p>Secret Anchor</p>")
    assert evaluate_stable_task(hidden, rules)["stable_task_pass"] is False
    assert evaluate_stable_task(visible, rules)["stable_task_pass"] is True


@pytest.mark.parametrize('length', [299,300,301,1024])
def test_metadata_title_length_cannot_expose_hidden_html(length):
    rules={'version':1,'schema':'body_text','minimum_records':0,
           'stable_text_anchors':[{'id':'body','alternatives':['Required body']}]}
    title='T'*length
    for hidden in (False,True):
        html=('<html><body>'+('<script>Required body</script><p>Other text</p>'
              if hidden else '<p>Required body</p>')+'</body></html>')
        direct=evaluate_stable_task(title+'\n'+html,rules)
        scored=score_attempt({'id':'article','url':'https://example.com/article','track':'web','task_kind':'body',
                              'acceptance_contract':rules},html.encode(),ok=True,title=title)
        assert direct['stable_task_pass'] is (not hidden)
        assert scored['stable_task_pass'] is (not hidden)
        assert direct['stable_anchor_results']==scored['stable_anchor_results']


def test_html_body_text_is_normalized_once_without_losing_title():
    from harness.web_fetch.dynamic_lists import _scoped_html
    rules={'version':1,'schema':'body_text','minimum_records':0,
           'stable_text_anchors':[{'id':'body','alternatives':['Required body']},
                                  {'id':'title','alternatives':['Page heading']}]}
    html='<html><body><p>Required <b>body</b></p><script>Access denied</script></body></html>'
    case={'id':'article','url':'https://example.com/article','track':'web','task_kind':'body','acceptance_contract':rules}
    with patch('harness.web_fetch.dynamic_lists._scoped_html',wraps=_scoped_html) as parser:
        scored=score_attempt(case,html.encode(),ok=True,title='Page heading')
    assert scored['stable_task_pass'] is True
    assert scored['blocked_page'] is False
    assert parser.call_count==1


@pytest.mark.parametrize('tag', ['strong', 'code', 'em', 'a', 'span'])
def test_inline_challenge_phrase_is_not_a_standalone_challenge(tag):
    rules={'version':1,'schema':'body_text','minimum_records':0,
           'stable_text_anchors':[{'id':'body','alternatives':['Required body']} ]}
    case={'id':'article','url':'https://example.com/article','track':'web','task_kind':'body','acceptance_contract':rules}
    body=f'<p>Required body. If you see <{tag}>Verify you are human</{tag}>, complete the verification before continuing.</p>'
    for html, expected in [(body, True), ('<p>Required body.</p><h1><strong>Verify you are human</strong></h1>', False)]:
        html='<html><body>'+html+'</body></html>'
        assert evaluate_stable_task(html,rules)['stable_task_pass'] is expected
        scored=score_attempt(case,html.encode(),ok=True)
        assert scored['content_pass'] is expected
        assert scored['blocked_page'] is (not expected)


@pytest.mark.parametrize('markup', ['<p>Required body.</p><p>Verify you are human</p>',
                                  '<p>Required body.<br>Verify you are human</p>',
                                  '<pre>Required body.\nVerify you are human</pre>'])
def test_rendered_line_breaks_preserve_real_challenge_markers(markup):
    case={'url':'https://example.test/article','task_kind':'body'}
    assert score_attempt(case,('<html><body>'+markup+'</body></html>').encode(),ok=True)['blocked_page'] is True


@pytest.mark.parametrize('visible', [False, True])
def test_document_head_cannot_supply_body_evidence(visible):
    rules={'version':1,'schema':'body_text','minimum_records':0,
           'stable_text_anchors':[{'id':'body','alternatives':['Required body']}]}
    html='<html><head><title>Required body</title></head><body><p>'+('Required body' if visible else 'Other text')+'</p></body></html>'
    case={'url':'https://example.test/article','task_kind':'body','acceptance_contract':rules}
    assert evaluate_stable_task(html,rules)['stable_task_pass'] is visible
    assert score_attempt(case,html.encode(),ok=True)['content_pass'] is visible


def test_numeric_card_pattern_can_describe_the_value_not_only_a_unit():
    from harness.web_fetch.composite_tasks import evaluate_composite_acceptance
    rules={'version':1,'groups':[{'id':'stock','kind':'named_link','minimum_records':1,
           'hosts':['example.test'],'entry_path':r'/item/[0-9]+',
           'start_pattern':r'^# Stock$','end_pattern':r'^# End$',
           'required_fields':[{'id':'quantity','kind':'integer','pattern':r'^\d+$'}]}]}
    text='# Stock\n[One](https://example.test/item/1)\n12\n# End'
    assert evaluate_composite_acceptance(text,rules,base_url='https://example.test/')['composite_acceptance_pass']
