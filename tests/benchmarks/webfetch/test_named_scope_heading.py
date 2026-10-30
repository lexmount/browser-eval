"""A section heading owns records; a matching navigation label does not."""
from lexbench_webfetch.scoring import score_attempt
from lexbench_webfetch.metrics import task_passed

CASE = {'url': 'https://example.test/search', 'acceptance_contract': {
    'version': 1, 'schema': 'named_link', 'minimum_records': 2,
    'hosts': ['example.test'], 'entry_path': r'/item/[0-9]+',
    'entry_scope': {'html_selector': '.results a[href]',
                    'start_pattern': '^Find games$', 'end_pattern': '^See all'}}}


def test_section_heading_beats_the_same_label_in_navigation():
    text = 'Find games\n[Help](/help)\nSee all\n\n### Find games\n[Alpha](/item/1)\n[Beta](/item/2)\nSee all'
    assert task_passed(True, score_attempt(CASE, text.encode(), ok=True))


def test_section_cannot_borrow_records_from_navigation_or_after_its_end():
    text = 'Find games\n[Alpha](/item/1)\n[Beta](/item/2)\nSee all\n\n### Find games\n[Gamma](/item/3)\nSee all\n[Delta](/item/4)'
    assert not task_passed(True, score_attempt(CASE, text.encode(), ok=True))


def test_nested_image_card_does_not_use_empty_list_markup_as_its_name():
    text = '### Find games\n- [![](/cover.jpg)\n\n - **\n\nAlpha game\n2026.3.26\n8,500円\nPublisher\n**](/item/1)\n- [Beta](/item/2)\nSee all'
    assert task_passed(True, score_attempt(CASE, text.encode(), ok=True))


def test_empty_card_with_only_artwork_and_layout_has_no_visible_name():
    text = '### Find games\n- [![](/cover.jpg)\n\n - **\n\n2026.3.26\n**](/item/1)\n- [Beta](/item/2)\nSee all'
    assert not task_passed(True, score_attempt(CASE, text.encode(), ok=True))
