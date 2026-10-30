"""Information requests may use a delivered page title, never unrelated metadata."""
import pytest
from lexbench_webfetch.scoring import score_attempt
from lexbench_webfetch.metrics import task_passed


def evaluate(output,scope='body_or_title',title=None):
    return score_attempt({'url':'https://example.test/video','required_facts':[
        {'id':'video-title','alternatives':['Example video'],'match_scope':scope},
        {'id':'publisher','alternatives':['Example Channel']} ]},output.encode(),ok=True,title=title)

@pytest.mark.parametrize('output,title',[
    ('Example video\nExample Channel',None),
    ('---\ntitle: "Example video - Player"\n---\nExample Channel',None),
    ("---\ntitle: 'Example video'\n---\nExample Channel",None),
    ('<html><head><title>Example video</title></head><body>Example Channel</body></html>',None),
    ('Example Channel','Example video'),
])
def test_declared_title_information_can_use_a_real_page_title(output,title):
    assert task_passed(True,evaluate(output,title=title))

@pytest.mark.parametrize('output',[
    '---\ntitle: "Other video"\nkeywords: "Example video"\n---\nExample Channel',
    '---\ntitle: "Example video"\nkeywords: "Example Channel"\n---\nOther body',
    '---\ntitle: "Example video"\ntitle: "Other video"\n---\nExample Channel',
])
def test_keywords_or_ambiguous_metadata_cannot_supply_information(output):
    assert not task_passed(True,evaluate(output))


def test_body_requirement_still_rejects_title_only_evidence():
    assert not task_passed(True,evaluate('---\ntitle: "Example video"\n---\nExample Channel',scope='body'))


def test_fact_cannot_be_joined_across_body_and_title():
    assert not task_passed(True,evaluate('Example Channel\nExample',title='video'))
