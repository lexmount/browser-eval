"""Declared card starts own fields before a linked title without borrowing peers."""
import pytest
from lexbench_webfetch.scoring import score_attempt
from lexbench_webfetch.metrics import task_passed

CASE={'url':'https://example.test/','composite_acceptance':{'version':1,'groups':[{
    'id':'events','kind':'named_link','hosts':['example.test'],'entry_path':r'/item/[0-9]+',
    'minimum_records':1,'record_start_pattern':r'(?m)^- 2026-10-[0-9]{2}$',
    'required_fields':[{'id':'date','kind':'date_or_time'},
                       {'id':'venue','kind':'text','aliases':['Venue']},
                       {'id':'region','kind':'text','aliases':['Region']}]}]}}

@pytest.mark.parametrize('body,expected',[
 ('- 2026-10-16\nVenue: Hall A\nRegion: Alabama\n[Concert](/item/1)',True),
 ('- 2026-10-16\nVenue: Hall A\nRegion: Alabama\n- 2026-10-17\n[Concert](/item/1)',False),
 ('- 2026-10-16\nVenue: Hall A\n[Concert](/item/1)\n- 2026-10-17\nRegion: Alabama\n[Other](/item/2)',False),
])
def test_fields_before_link_belong_only_to_the_declared_card(body,expected):
    assert task_passed(True,score_attempt(CASE,body.encode(),ok=True)) is expected
