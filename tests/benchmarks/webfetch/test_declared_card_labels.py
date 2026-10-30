import pytest
from lexbench_webfetch.scoring import score_attempt
from lexbench_webfetch.metrics import task_passed

CASE={'url':'https://example.test/','composite_acceptance':{'version':1,'groups':[{'id':'events','kind':'named_link','hosts':['example.test'],'entry_path':r'/item/[0-9]+','minimum_records':1,'markdown_label_pattern':r'(?m)^(?:Today\n)?2026-10-16\n(?P<label>[^\n]+)\n','required_fields':[{'id':'date','kind':'date_or_time'},{'id':'venue','kind':'text','minimum_chars':2},{'id':'region','kind':'text','pattern':r',\s*AL\b'}]}]}}
@pytest.mark.parametrize('label,passed',[
 ('Today\n2026-10-16\nEvening Concert\nExample Hall Tuscaloosa, AL',True),
 ('2026-10-16\nEvening Concert\nExample Hall Tuscaloosa, AL',True),
 ('Today\n2026-10-16\nExample Hall Tuscaloosa, AL',False),
 ('Today\n2026-10-16\nEvening Concert\n, AL',False),
 ('Today\n2026-10-16\nEvening Concert\nExample Hall',False),
])
def test_declared_multiline_card_label_and_owned_fields(label,passed):
 result=score_attempt(CASE,('[![](/cover.jpg)\n'+label+'](/item/1)').encode(),ok=True)
 assert task_passed(True,result) is passed
