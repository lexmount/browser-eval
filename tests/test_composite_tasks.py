import pytest

from harness.web_fetch.composite_tasks import (
    evaluate_composite_acceptance,
    validate_composite_acceptance,
)


def contract(*groups):
    return {"version": 1, "groups": list(groups)}


def named(identifier, start, end, minimum, **extra):
    return {
        "id": identifier,
        "kind": "named_link",
        "minimum_records": minimum,
        "hosts": ["example.test"],
        "entry_path": r"/item/[0-9]+",
        "start_pattern": start,
        "end_pattern": end,
        **extra,
    }


def test_independent_groups_cannot_share_surplus_records():
    rules = contract(
        named("first", r"^# First$", r"^# Second$", 2),
        named("second", r"^# Second$", r"^# End$", 2),
    )
    text = """# First
- [One](https://example.test/item/1)
- [Two](https://example.test/item/2)
- [Three](https://example.test/item/3)
# Second
- [Four](https://example.test/item/4)
# End
"""
    result = evaluate_composite_acceptance(text, rules, base_url="https://example.test/")
    assert not result["composite_acceptance_pass"]
    assert [row["count"] for row in result["composite_group_results"]] == [3, 1]


def test_required_fields_must_belong_to_each_link_card():
    rules = contract(
        named(
            "events",
            r"^# Events$",
            r"^# End$",
            2,
            required_fields=[
                {"id": "venue", "kind": "text", "aliases": ["Venue"]},
                {"id": "date", "kind": "date_or_time", "aliases": ["Date"]},
            ],
        )
    )
    positive = """# Events
[One](https://example.test/item/1)
Venue: Hall A
Date: 2026-09-28

[Two](https://example.test/item/2)
Venue: Hall B
Date: 2026-09-29

# End
"""
    negative = positive.replace("Venue: Hall B\n", "")
    assert evaluate_composite_acceptance(positive, rules, base_url="https://example.test/")[
        "composite_acceptance_pass"
    ]
    failed = evaluate_composite_acceptance(negative, rules, base_url="https://example.test/")
    assert not failed["composite_acceptance_pass"]
    assert failed["composite_group_results"][0]["count"] == 1


def test_later_duplicate_does_not_lend_fields_to_intervening_record():
    rules = contract(
        named(
            "events",
            r"^# Events$",
            r"^# End$",
            2,
            required_fields=[{"id": "date", "kind": "date_or_time", "aliases": ["Date"]}],
        )
    )
    text = """# Events
[One](https://example.test/item/1)
Date: 2026-09-28

[Two](https://example.test/item/2)

[One again](https://example.test/item/1)
Date: 2026-09-29
# End
"""
    result = evaluate_composite_acceptance(text, rules, base_url="https://example.test/")
    assert not result["composite_acceptance_pass"]
    assert result["composite_group_results"][0]["count"] == 1


def test_record_line_pattern_keeps_support_links_inside_the_article_card():
    content = """\
## [First article](https://news.example/first)

3 hours ago

[Neutral](/about-labels#neutral) First Source

Owner: [First Owner](https://owners.example/first)

## [Second article](https://news.example/second)

an hour ago

[Center-left](/about-labels#center-left) Second Source

Owner: [Second Owner](https://owners.example/second)
"""
    result = evaluate_composite_acceptance(
        content,
        {
            "version": 1,
            "groups": [
                {
                    "id": "articles",
                    "kind": "named_link",
                    "minimum_records": 2,
                    "hosts": ["*"],
                    "entry_path": "/.+",
                    "record_line_pattern": r"^##\s+",
                    "required_fields": [
                        {"id": "time", "kind": "date_or_time"},
                        {
                            "id": "label",
                            "kind": "text",
                            "pattern": "Neutral|Center-left",
                        },
                        {"id": "source", "kind": "text", "minimum_chars": 2},
                    ],
                }
            ],
        },
        base_url="https://page.example/",
    )

    assert result["composite_acceptance_pass"] is True
    assert result["composite_group_results"][0]["count"] == 2


def test_scoped_repeated_titles_close_cards_without_borrowing_navigation():
    rules = {
        "version": 1,
        "groups": [{
            "id": "cards",
            "kind": "name_description",
            "minimum_records": 2,
            "start_pattern": r"^# Catalog$",
            "end_pattern": r"^# End$",
        }],
    }
    cards = (
        "# Catalog\n\nAlpha film\n\nA complete first description.\n\nAlpha film\n\n"
        "Beta film\n\nA complete second description.\n\nBeta film\n\n# End"
    )
    result = evaluate_composite_acceptance(cards, rules, base_url="https://example.test")
    assert result["composite_acceptance_pass"] is True
    assert result["composite_group_results"][0]["count"] == 2

    outside = (
        "Home\n\nExplore everything we offer today.\n\nHome\n\n"
        "About\n\nLearn why customers choose us.\n\nAbout\n\n"
        "# Catalog\n\nAlpha film\n\nA complete first description.\n\nAlpha film\n\n# End"
    )
    result = evaluate_composite_acceptance(outside, rules, base_url="https://example.test")
    assert result["composite_acceptance_pass"] is False
    assert result["composite_group_results"][0]["count"] == 1


def test_first_repeated_link_does_not_replace_the_visible_record_name():
    rules = contract(
        named(
            "homes",
            r"^# Homes$",
            r"^# End$",
            1,
            required_fields=[{"id": "price", "kind": "price"}],
        )
    )
    text = """# Homes
- [$900,000](https://example.test/item/1)
  [One Main Street](https://example.test/item/1)
# End
"""
    result = evaluate_composite_acceptance(text, rules, base_url="https://example.test/")
    assert result["composite_acceptance_pass"]
    assert result["composite_group_results"][0]["records"][0]["title"] == "One Main Street"


def test_section_scope_prefers_visible_heading_over_navigation_label():
    rules = contract(named("news", "News", "End", 1))
    text = """[News](/nav) [End](/end)
# News
[Story](https://example.test/item/1)
# End
"""
    assert evaluate_composite_acceptance(text, rules, base_url="https://example.test/")[
        "composite_acceptance_pass"
    ]


def test_named_link_accepts_a_visible_date_prefix_on_the_same_list_line():
    rules = contract(named("recent", "Recent", "End", 1))
    text = """# Recent
- 09/21/2026 - [Story](https://example.test/item/1)
# End
"""
    assert evaluate_composite_acceptance(text, rules, base_url="https://example.test/")[
        "composite_acceptance_pass"
    ]


def test_required_text_fields_cannot_reuse_one_line():
    rules = contract(
        named(
            "events",
            r"^# Events$",
            r"^# End$",
            1,
            required_fields=[
                {"id": "venue", "kind": "text"},
                {"id": "city", "kind": "text"},
            ],
        )
    )
    text = """# Events
[One](https://example.test/item/1)
Only one extra line

# End
"""
    assert not evaluate_composite_acceptance(text, rules, base_url="https://example.test/")[
        "composite_acceptance_pass"
    ]


def test_linked_table_requires_link_and_fields_in_same_row():
    rules = contract(
        {
            "id": "packages",
            "kind": "linked_table",
            "minimum_records": 2,
            "hosts": ["example.test"],
            "entry_path": r"/item/[0-9]+",
            "required_columns": [
                {"id": "name", "kind": "text", "aliases": ["Name"]},
                {"id": "description", "kind": "text", "aliases": ["Description"]},
                {"id": "owner", "kind": "text", "aliases": ["Owner"]},
            ],
            "identity_column": "name",
            "link_column": "name",
        }
    )
    positive = """| Name | Description | Owner |
|---|---|---|
| [One](https://example.test/item/1) | First | Alice |
| [Two](https://example.test/item/2) | Second | Bob |
"""
    assert evaluate_composite_acceptance(positive, rules, base_url="https://example.test/")[
        "composite_acceptance_pass"
    ]
    assert not evaluate_composite_acceptance(
        positive.replace("| Second | Bob |", "| | Bob |"), rules, base_url="https://example.test/"
    )["composite_acceptance_pass"]


def test_field_report_binds_fixed_values_to_their_labels():
    rules = contract(
        {
            "id": "report",
            "kind": "field_report",
            "required_fields": [
                {"id": "page_id", "kind": "integer", "aliases": ["ID"]},
                {"id": "wikidata", "kind": "text", "aliases": ["Wikidata ID"]},
            ],
            "fixed_values": {"page_id": "1164", "wikidata": "Q11660"},
        }
    )
    positive = "ID: 1164\nWikidata ID: Q11660\n"
    assert evaluate_composite_acceptance(positive, rules, base_url="https://example.test/")[
        "composite_acceptance_pass"
    ]
    assert not evaluate_composite_acceptance(
        "ID: 999\nWikidata ID: Q11660\nElsewhere 1164", rules, base_url="https://example.test/"
    )["composite_acceptance_pass"]


def test_field_report_cannot_reuse_one_unlabelled_line_for_multiple_fields():
    rules = contract(
        {
            "id": "report",
            "kind": "field_report",
            "required_fields": [
                {"id": "first", "kind": "integer"},
                {"id": "second", "kind": "integer"},
            ],
        }
    )
    assert not evaluate_composite_acceptance(
        "Only one value: 42", rules, base_url="https://example.test/"
    )["composite_acceptance_pass"]


def test_field_report_cannot_reuse_next_line_for_duplicate_labels():
    rules = contract(
        {
            "id": "report",
            "kind": "field_report",
            "required_fields": [
                {"id": "first", "kind": "integer", "aliases": ["Count"]},
                {"id": "second", "kind": "integer", "aliases": ["Count"]},
            ],
        }
    )
    assert not evaluate_composite_acceptance("Count\n42", rules, base_url="https://example.test/")[
        "composite_acceptance_pass"
    ]


def test_table_fields_are_checked_on_the_same_row_and_deduplicated():
    rules = contract(
        {
            "id": "problems",
            "kind": "table",
            "minimum_records": 2,
            "required_columns": [
                {"id": "number", "kind": "integer", "aliases": ["No.", "Number"]},
                {"id": "name", "kind": "text", "aliases": ["Name"]},
                {"id": "attempts", "kind": "integer", "aliases": ["Attempts"]},
                {"id": "accepted", "kind": "integer", "aliases": ["AC"]},
            ],
            "identity_column": "number",
        }
    )
    positive = """| No. | Name | Attempts | AC |
|---|---|---|---|
| 1 | Alpha | 12 | 4 |
| 2 | Beta | 10 | 5 |
"""
    negative = positive.replace("| 2 | Beta | 10 | 5 |", "| 2 | Beta | 10 | |")
    assert evaluate_composite_acceptance(positive, rules, base_url="https://example.test/")[
        "composite_acceptance_pass"
    ]
    assert not evaluate_composite_acceptance(negative, rules, base_url="https://example.test/")[
        "composite_acceptance_pass"
    ]


def test_table_column_types_reject_nonempty_fake_values():
    rules = contract(
        {
            "id": "packages",
            "kind": "linked_table",
            "minimum_records": 1,
            "hosts": ["example.test"],
            "entry_path": r"/item/[0-9]+",
            "required_columns": [
                {"id": "name", "kind": "text", "aliases": ["Name"]},
                {"id": "version", "kind": "version", "aliases": ["Version"]},
                {"id": "date", "kind": "date_or_time", "aliases": ["Date"]},
            ],
            "identity_column": "name",
            "link_column": "name",
        }
    )
    valid = """| Name | Version | Date |
|---|---|---|
| [One](https://example.test/item/1) | 1.2.3 | 2026-09-29 |
"""
    invalid = valid.replace("1.2.3 | 2026-09-29", "fake | fake")
    assert evaluate_composite_acceptance(valid, rules, base_url="https://example.test/")[
        "composite_acceptance_pass"
    ]
    result = evaluate_composite_acceptance(invalid, rules, base_url="https://example.test/")
    assert not result["composite_acceptance_pass"]
    assert result["composite_group_results"][0]["count"] == 0


def test_adjacent_text_does_not_repair_a_literal_group():
    rules = contract(
        {
            "id": "edition",
            "kind": "literals",
            "required_literals": ["97 pages, Hardcover", "First published January 1, 1970"],
        }
    )
    assert not evaluate_composite_acceptance(
        "97 pages, Hardcover", rules, base_url="https://example.test/"
    )["composite_acceptance_pass"]


def test_invalid_contract_fails_closed():
    with pytest.raises(ValueError, match="positive minimum"):
        validate_composite_acceptance(
            contract(
                {
                    "id": "bad",
                    "kind": "name_description",
                    "minimum_records": 0,
                }
            )
        )
