import pytest
from lexbench_webfetch.scoring import score_attempt
from lexbench_webfetch.metrics import task_passed

FIELDS=[{'id':'price','kind':'price'},{'id':'bedrooms','kind':'integer','pattern':r'\bbds\b'},{'id':'bathrooms','kind':'integer','pattern':r'\bba\b'},{'id':'area','kind':'area'}]
def score(body, fields=FIELDS):
 return score_attempt({'url':'https://example.test/','min_content_chars':1,'composite_acceptance':{'version':1,'groups':[{'id':'cards','kind':'named_link','hosts':['example.test'],'entry_path':r'/item/[0-9]+','minimum_records':1,'required_fields':fields}]}},body.encode(),ok=True)

@pytest.mark.parametrize('body,expected',[
 ('- [$900](/item/1)\n\n- **3** bds\n- **2** ba\n- **900** sqft\n\nActive[House A](/item/1)',True),
 ('- [House A](/item/1)\n\n- [House B](/item/2)\n- $900\n- 3 bds\n- 2 ba\n',False),
 ('- [House A](/item/1)\n\n# Other section\n- $900\n- 3 bds\n- 2 ba\n- 900 sqft',False),
 ('- [House A](/item/1)\n\n- unrelated prose\n- $900\n- 3 bds\n- 2 ba\n- 900 sqft',False),
])
def test_owned_field_bullets(body,expected):
 assert task_passed(True,score(body)) is expected

def test_text_pattern_consumes_distinct_spans_without_losing_lookahead():
 fields=[{'id':'venue','kind':'text','pattern':r'^Hall A(?=\s+Town, TX$)'},{'id':'city','kind':'text','pattern':r'Town, TX$'}]
 assert task_passed(True,score('[Event](/item/1)\nHall A Town, TX',fields))
 assert not task_passed(True,score('[Event](/item/1)\nHall A',fields))
 assert not task_passed(True,score('[Event](/item/1)\nTown, TX',fields))
