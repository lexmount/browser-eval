import pytest

from lexbench_webfetch.composite_tasks import evaluate_composite_acceptance, validate_composite_acceptance


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


@pytest.mark.parametrize("destinations,extra,count", [
    (["https://a.test/item/1", "https://b.test/item/1"], {}, 2),
    (["https://a.test/item/1", "https://a.test/item/1/#details"], {}, 1),
    (["https://a.test/item/1?q=a", "https://a.test/item/1?q=b"], {}, 1),
    (["https://a.test/item/1?q=a", "https://a.test/item/1?q=b"],
     {"identity_query_keys": ["q"]}, 2),
])
def test_wildcard_links_count_distinct_destinations(destinations, extra, count):
    rules = contract(named("news", r"^# News$", r"^# End$", 2,
                           hosts=["*"], **extra))
    text = "# News\n" + "\n".join(
        f"[Story {index}]({destination})"
        for index, destination in enumerate(destinations)) + "\n# End"
    result = evaluate_composite_acceptance(text, rules, base_url="https://example.test/")
    assert result["composite_group_results"][0]["count"] == count
    assert result["composite_group_results"][0]["records"][0]["title"] == "Story 0"
    assert result["composite_acceptance_pass"] == (count >= 2)


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


def test_linearized_linked_table_preserves_rows_without_borrowing_fields():
    rules = contract({
        "id": "repositories", "kind": "linked_table", "minimum_records": 2,
        "hosts": ["example.test"], "entry_path": r"/repo/[a-z]+",
        "required_columns": [
            {"id": "name", "kind": "text", "aliases": ["Name"]},
            {"id": "description", "kind": "text", "aliases": ["Description"]},
            {"id": "owner", "kind": "text", "aliases": ["Owner"]},
        ],
        "identity_column": "name", "link_column": "name",
    })
    complete = """[Name](/sort/name)\n[Description](/sort/description)\n[Owner](/sort/owner)\n\n[alpha](/repo/alpha)\n[Alpha summary](/repo/alpha)\n[Alice](/owner/alice)\n\n[beta](/repo/beta)\n[Beta summary](/repo/beta)\n[Bob](/owner/bob)"""
    assert evaluate_composite_acceptance(
        complete, rules, base_url="https://example.test/"
    )["composite_acceptance_pass"]
    for missing in ("[Beta summary](/repo/beta)\n", "[Bob](/owner/bob)"):
        incomplete = complete.replace(missing, "")
        if missing == "[Bob](/owner/bob)":
            incomplete += "\n2 months\n[summary](/repo/beta)"
        assert not evaluate_composite_acceptance(
            incomplete, rules, base_url="https://example.test/"
        )["composite_acceptance_pass"]


def test_labeled_fields_may_share_a_line_but_stay_inside_their_card():
    rules = contract(named(
        "news", r"^# News$", r"^# End$", 2,
        required_fields=[
            {"id": "published", "kind": "date_or_time", "aliases": ["Published"]},
            {"id": "comments", "kind": "integer", "aliases": ["Comments"]},
        ],
    ))
    complete = """# News\n[First](/item/1)\nPublished: Tue 08:30 UTC Comments: 0\n[Second](/item/2)\nPublished: Wed 09:45 UTC Comments: 3\n# End"""
    assert evaluate_composite_acceptance(
        complete, rules, base_url="https://example.test/"
    )["composite_acceptance_pass"]
    assert not evaluate_composite_acceptance(
        complete.replace("Comments: 0", "Comments:"),
        rules, base_url="https://example.test/",
    )["composite_acceptance_pass"]


def test_labeled_fields_accept_unpunctuated_values_on_independent_lines():
    rules = contract(named(
        "news", r"^# News$", r"^# End$", 2,
        required_fields=[
            {"id": "published", "kind": "date_or_time", "aliases": ["Published"]},
            {"id": "comments", "kind": "integer", "aliases": ["Comments"]},
        ],
    ))
    content = """# News
[First](/item/1)
Published Tue 08:30 UTC
Comments 0
[Second](/item/2)
Published Wed 09:45 UTC
Comments 3
# End"""
    assert evaluate_composite_acceptance(
        content, rules, base_url="https://example.test/"
    )["composite_acceptance_pass"]

    dashed = content.replace("Published ", "Published - ").replace(
        "Comments ", "Comments - "
    )
    assert evaluate_composite_acceptance(
        dashed, rules, base_url="https://example.test/"
    )["composite_acceptance_pass"]


def test_standalone_labels_do_not_lend_one_following_value_to_other_fields():
    rules = contract(named(
        "news", r"^# News$", r"^# End$", 1,
        required_fields=[
            {"id": "publisher", "kind": "text", "aliases": ["Publisher"]},
            {"id": "comments", "kind": "integer", "aliases": ["Comments"]},
        ],
    ))
    label_after_label = """# News\n[First](/item/1)\nPublisher\nComments: 3\n# End"""
    assert not evaluate_composite_acceptance(
        label_after_label, rules, base_url="https://example.test/"
    )["composite_acceptance_pass"]

    unpunctuated_label_after_label = """# News
[First](/item/1)
Publisher
Comments 3
# End"""
    assert not evaluate_composite_acceptance(
        unpunctuated_label_after_label, rules, base_url="https://example.test/"
    )["composite_acceptance_pass"]

    repeated = contract(named(
        "news", r"^# News$", r"^# End$", 1,
        required_fields=[
            {"id": "first", "kind": "integer", "aliases": ["Count"]},
            {"id": "second", "kind": "integer", "aliases": ["Count"]},
        ],
    ))
    assert not evaluate_composite_acceptance(
        "# News\n[First](/item/1)\nCount\n3\n# End",
        repeated, base_url="https://example.test/",
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


def test_field_report_reads_owned_markdown_table_cells_before_fixed_value_checks():
    rules = contract(
        {
            "id": "report",
            "kind": "field_report",
            "required_fields": [
                {"id": "page_id", "kind": "integer", "aliases": ["ID"]},
                {
                    "id": "wikidata",
                    "kind": "text",
                    "aliases": ["Wikidata ID"],
                    "pattern": r"Q[0-9]+",
                },
                {"id": "pageviews", "kind": "integer", "aliases": ["Pageviews"]},
            ],
            "fixed_values": {
                "page_id": "1164",
                "wikidata": "Q11660",
                "pageviews": "567652",
            },
        }
    )
    positive = """|  |  |
| --- | --- |
| ID | [1164](/page/1164) |
| Wikidata ID | [Q11660](/entity/Q11660) · 214 sitelinks |
| Pageviews (30 days) | [567,652](/views) |
"""
    assert evaluate_composite_acceptance(
        positive, rules, base_url="https://example.test/"
    )["composite_acceptance_pass"]
    wrong_owned_cells = positive.replace("[1164](/page/1164)", "999").replace(
        "214 sitelinks", "214 sitelinks; unrelated note 1164"
    )
    assert not evaluate_composite_acceptance(
        wrong_owned_cells, rules, base_url="https://example.test/"
    )["composite_acceptance_pass"]


def test_scoped_field_report_accepts_single_cell_section_heading():
    rules = contract({"id": "statistics", "kind": "field_report", "start_pattern": r'^Prose$',
        "end_pattern": r'^End$', "required_fields": [{"id": "words", "kind": "integer", "aliases": ["Words"]}]})
    positive = '| Prose |\n| --- |\nWords 12604\nEnd'
    assert evaluate_composite_acceptance(positive, rules, base_url='https://example.test/')['composite_acceptance_pass']
    assert not evaluate_composite_acceptance(positive.replace('| Prose |','| Other |'), rules, base_url='https://example.test/')['composite_acceptance_pass']


def test_scoped_statistics_allow_adjacent_native_controls():
    rules = contract({"id": "statistics", "kind": "field_report",
        "start_pattern": r"^(?:#{1,6}\s*)?Statistics(?:\s+(?:\\)?\[(?:show|hide)(?:\\)?\])*$",
        "end_pattern": r"^End$", "required_fields": [{"id": "id", "kind": "integer", "aliases": ["ID"]}]})
    for heading in ('### Statistics [show][hide]', r'### Statistics \[show\]\[hide\]'):
        positive = heading + '\nID 1164\nEnd'
        assert evaluate_composite_acceptance(positive, rules, base_url='https://example.test/')['composite_acceptance_pass']
        negative = positive.replace('Statistics', 'Different section')
        assert not evaluate_composite_acceptance(negative, rules, base_url='https://example.test/')['composite_acceptance_pass']


def test_field_report_period_qualifier_is_not_the_value():
    rules = contract({"id": "views", "kind": "field_report", "required_fields": [
        {"id": "views", "kind": "integer", "aliases": ["Pageviews"]}]})
    positive = evaluate_composite_acceptance('Pageviews (30 days)580,281', rules, base_url='https://example.test/')
    assert positive['composite_acceptance_pass']
    assert positive['composite_group_results'][0]['records'][0]['fields']['views'] == '580,281'
    assert not evaluate_composite_acceptance('Pageviews (30 days)', rules, base_url='https://example.test/')['composite_acceptance_pass']


def test_html_void_image_cannot_swallow_the_section_end():
    rules = contract({"id": "articles", "kind": "named_link", "minimum_records": 1,
        "hosts": ["example.test"], "entry_path": "/article/.+", "start_pattern": r"^## News\s*$",
        "end_pattern": "Next section", "required_fields": [{"id": "date", "kind": "date_or_time"}]})
    positive = '<html><body><h2>News</h2><article><a href="/article/one">First article</a><p>Oct. 9, 2026</p></article><h2><img src="/logo.png" alt="Logo">Next section</img></h2></body></html>'
    result = evaluate_composite_acceptance(positive, rules, base_url="https://example.test/", table_content=positive, native_html=positive)
    assert result['composite_acceptance_pass']
    negative = positive.replace('alt="Logo">Next section', 'alt="Next section">')
    result = evaluate_composite_acceptance(negative, rules, base_url="https://example.test/", table_content=negative, native_html=negative)
    assert not result['composite_acceptance_pass']


def test_date_before_title_belongs_to_same_target_image_card():
    rules = contract({"id": "articles", "kind": "named_link", "minimum_records": 1,
        "hosts": ["example.test"], "entry_path": "/article/.+",
        "required_fields": [{"id": "time", "kind": "date_or_time"}]})
    positive = "[![](/image.png)](/article/first)\n\n4 hours ago\n[First article](/article/first)\n"
    assert evaluate_composite_acceptance(positive, rules, base_url="https://example.test/")["composite_acceptance_pass"]
    negative = "[Previous article](/article/previous)\n\n4 hours ago\n[![](/image.png)](/article/first)\n\n[First article](/article/first)\n"
    # The earlier card owns its date; the image cannot move it to the next one.
    result = evaluate_composite_acceptance(negative, rules, base_url="https://example.test/")
    assert all(row['url'] != 'https://example.test/article/first' for row in result['composite_group_results'][0]['records'])


def test_field_report_preserves_linked_value_boundary():
    rules = contract({"id": "report", "kind": "field_report", "required_fields": [
        {"id": "page_id", "kind": "integer", "aliases": ["ID"]},
        {"id": "wikidata", "kind": "text", "aliases": ["Wikidata ID"], "pattern": r"Q[0-9]+"}],
        "fixed_values": {"page_id": "1164", "wikidata": "Q11660"}})
    positive = "ID[1164](/page)\nWikidata ID[Q11660](/entity) · 214 sitelinks"
    assert evaluate_composite_acceptance(positive, rules, base_url="https://example.test/")["composite_acceptance_pass"]
    for negative in (positive.replace("ID[1164]", "OtherID[1164]"), positive.replace("[Q11660]", "[Q99999]")):
        assert not evaluate_composite_acceptance(negative, rules, base_url="https://example.test/")["composite_acceptance_pass"]


def test_field_report_prefers_the_longest_declared_label():
    rules = contract(
        {
            "id": "report",
            "kind": "field_report",
            "required_fields": [
                {"id": "page_id", "kind": "integer", "aliases": ["ID"]},
            ],
            "fixed_values": {"page_id": "1164"},
        }
    )
    assert not evaluate_composite_acceptance(
        "Wikidata ID: Q1164 · 214 sitelinks",
        rules,
        base_url="https://example.test/",
    )["composite_acceptance_pass"]


def test_field_report_explicit_fields_may_share_one_line_without_sharing_values():
    rules = contract(
        {
            "id": "report",
            "kind": "field_report",
            "required_fields": [
                {"id": "page_id", "kind": "integer", "aliases": ["ID"]},
                {
                    "id": "wikidata",
                    "kind": "text",
                    "aliases": ["Wikidata ID"],
                    "pattern": r"Q[0-9]+",
                },
            ],
            "fixed_values": {"page_id": "1164", "wikidata": "Q11660"},
        }
    )
    assert evaluate_composite_acceptance(
        "ID: 1164 Wikidata ID: Q11660",
        rules,
        base_url="https://example.test/",
    )["composite_acceptance_pass"]
    assert not evaluate_composite_acceptance(
        "ID: 1164 Wikidata ID:",
        rules,
        base_url="https://example.test/",
    )["composite_acceptance_pass"]


def test_field_value_equal_to_its_label_consumes_the_owned_value_span():
    rules = contract(named(
        "items", r"^# Items$", r"^# End$", 1,
        required_fields=[
            {"id": "owner", "kind": "text", "aliases": ["Owner"]},
            {"id": "comments", "kind": "integer", "aliases": ["Comments"]},
        ],
    ))
    assert evaluate_composite_acceptance(
        "# Items\n[First](/item/1)\nOwner: Owner Comments: 3\n# End",
        rules,
        base_url="https://example.test/",
    )["composite_acceptance_pass"]


def test_field_report_section_scope_requires_the_declared_subfield():
    rules = contract(
        {
            "id": "prose",
            "kind": "field_report",
            "start_pattern": r"^Prose$",
            "end_pattern": r"^Registered edits$",
            "required_fields": [
                {"id": "words", "kind": "integer", "aliases": ["Words"]},
                {"id": "references", "kind": "integer", "aliases": ["References"]},
            ],
        }
    )
    complete = """Other section
Bytes
999999
Prose
Bytes
84958
Words
12599
References
558
Registered edits"""
    assert evaluate_composite_acceptance(
        complete, rules, base_url="https://example.test/"
    )["composite_acceptance_pass"]
    assert not evaluate_composite_acceptance(
        complete.replace("Words\n12599\n", ""), rules, base_url="https://example.test/"
    )["composite_acceptance_pass"]


@pytest.mark.parametrize(
    ("general", "prose", "registered"),
    [
        ("General statistics", "Prose", "Registered edits"),
        ("## General statistics", "## Prose", "## Registered edits"),
        ("##General statistics", "#Prose", "###Registered edits · 100"),
        (r"#### General statistics \[show\] \[hide\]", "Prose", "Registered edits: 100"),
    ],
)
def test_field_report_scope_accepts_plain_or_markdown_heading_boundaries(
    general, prose, registered
):
    general_pattern = (
        r"^(?:#{1,6}\s*)?General statistics"
        r"(?:\s+(?:\\)?\[(?:show|hide)(?:\\)?\])*$"
    )
    prose_pattern = r"^(?:#{1,6}\s*)?Prose\s*$"
    registered_pattern = (
        r"^(?:#{1,6}\s*)?Registered edits(?:\s*(?:·|:|[-–—])\s*.*)?$"
    )
    rules = contract(
        {
            "id": "general",
            "kind": "field_report",
            "start_pattern": general_pattern,
            "end_pattern": prose_pattern,
            "required_fields": [
                {"id": "page_id", "kind": "integer", "aliases": ["ID"]},
            ],
            "fixed_values": {"page_id": "1164"},
        },
        {
            "id": "prose",
            "kind": "field_report",
            "start_pattern": prose_pattern,
            "end_pattern": registered_pattern,
            "required_fields": [
                {"id": "words", "kind": "integer", "aliases": ["Words"]},
                {"id": "references", "kind": "integer", "aliases": ["References"]},
            ],
        },
    )
    content = f"""General statistics
Navigation only
{general}
ID | 1164
{prose}
Bytes 84958
Words 12599
References 558
{registered}
Words 999999"""
    assert evaluate_composite_acceptance(
        content, rules, base_url="https://example.test/"
    )["composite_acceptance_pass"]
    assert not evaluate_composite_acceptance(
        content.replace(registered, "Registered editors"),
        rules,
        base_url="https://example.test/",
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


@pytest.mark.parametrize("date", [
    "Jan. 14, 2026", "Feb. 14, 2026", "Mar. 14, 2026", "Apr. 14, 2026",
    "May 14, 2026", "Jun. 14, 2026", "Jul. 14, 2026", "Aug. 14, 2026",
    "Sep. 14, 2026", "Oct. 14, 2026", "Nov. 14, 2026", "Dec. 14, 2026",
    "14 Oct. 2026", "Oct 14, 2026", "October 14, 2026", "14 October 2026",
])
def test_article_dates_accept_english_month_abbreviation_punctuation(date):
    from lexbench_webfetch.metrics import task_passed
    from lexbench_webfetch.scoring import score_attempt

    case = {
        "url": "https://example.test/news",
        "composite_acceptance": contract(named(
            "news", r"^# News$", r"^# End$", 1,
            required_fields=[{"id": "published", "kind": "date_or_time"}],
        )),
    }
    body = f"# News\n[Article](https://example.test/item/1)\n{date}\n# End"
    assert task_passed(True, score_attempt(case, body.encode(), ok=True))


@pytest.mark.parametrize("date", ["October", "Oct.", "2026", "No publication date"])
def test_article_date_requires_a_day_or_time_not_just_month_or_year(date):
    from lexbench_webfetch.metrics import task_passed
    from lexbench_webfetch.scoring import score_attempt

    case = {
        "url": "https://example.test/news",
        "composite_acceptance": contract(named(
            "news", r"^# News$", r"^# End$", 1,
            required_fields=[{"id": "published", "kind": "date_or_time"}],
        )),
    }
    body = f"# News\n[Article](https://example.test/item/1)\n{date}\n# End"
    assert not task_passed(True, score_attempt(case, body.encode(), ok=True))


@pytest.mark.parametrize('content,minimum,expected', [
    ('<article><header><a href="/item/1">First House</a><p>$100,000</p></header><section><p>900 sqft</p></section></article>',1,True),
    ('<article><a href="/item/1">First House</a><p>$100,000</p><aside><p>Unrelated notice</p></aside><p>900 sqft</p></article>',1,True),
    ('<article><a href="/item/1">First House</a><p>$100,000</p><p>900 sqft</p></article>',1,True),
    ('<article><p>$100,000</p><a href="/item/1">First House</a><p>900 sqft</p></article>',1,True),
    ('<article><a href="/item/1">First House</a><p>$100,000</p></article><article><p>900 sqft</p><a href="/item/2">Second House</a></article>',1,False),
    ('<ul><li><a href="/item/1">First House</a><p>$100,000</p></li><li><p>900 sqft</p><a href="/item/2">Second House</a></li></ul>',1,False),
    ('<div><div><a href="/item/1">First House</a><p>$100,000</p></div><div><p>900 sqft</p><a href="/item/2">Second House</a></div></div>',1,False),
    ('<article><a href="/item/1">First House</a><p>$100,000</p></article><aside><p>900 sqft</p></aside>',1,False),
    ('<article><a href="/item/1">First House</a><p>$100,000</p><aside><p>900 sqft</p></aside></article>',1,False),
    ('<article><a href="/item/1">First House</a><p>$100,000</p></article><article><p>900 sqft</p><a href="/item/2"><img src="/cover.png" alt="Second House"></a></article>',1,False),
    ('<article><p>$100,000</p><a href="/item/1">First House</a><p>900 sqft</p></article><article><p>$200,000</p><a href="/item/2">Second House</a><p>1000 sqft</p></article>',2,True),
])
def test_public_scorer_keeps_html_fields_with_their_record_owner(content, minimum, expected):
    from lexbench_webfetch.scoring import score_attempt
    from lexbench_webfetch.metrics import task_passed
    rules=contract(named('houses',None,None,minimum,required_fields=[
        {'id':'price','kind':'price'},{'id':'area','kind':'area'}]))
    rules['groups'][0].pop('start_pattern');rules['groups'][0].pop('end_pattern')
    case={'url':'https://example.test/catalog','task_kind':'body','min_content_chars':1,'composite_acceptance':rules}
    metrics=score_attempt(case,content.encode(),ok=True)
    assert metrics['composite_acceptance_pass'] is expected
    assert task_passed(True,metrics,'body') is expected


@pytest.mark.parametrize('container', ['article','li','div'])
@pytest.mark.parametrize('wrappers', [0,1,3])
@pytest.mark.parametrize('field_tags', ['paragraphs','attribute_list'])
@pytest.mark.parametrize('complete', [False,True])
def test_html_record_layout_wrappers_and_attribute_lists_preserve_verdict(container, wrappers, field_tags, complete):
    from lexbench_webfetch.scoring import score_attempt
    from lexbench_webfetch.metrics import task_passed
    def fields(values):
        if field_tags=='attribute_list':
            return '<ul>'+''.join('<li>'+value+'</li>' for value in values)+'</ul>'
        return ''.join('<p>'+value+'</p>' for value in values)
    first=('<a href="/item/1">First House</a>'
           +fields(['$100,000','900 sqft'] if complete else ['$100,000']))
    second=(fields(['$200,000','1000 sqft'] if complete else ['900 sqft'])
            +'<a href="/item/2">Second House</a>')
    for _ in range(wrappers):
        first='<div class="content">'+first+'</div>'
        second='<div class="content">'+second+'</div>'
    content='<main><'+container+'>'+first+'</'+container+'><'+container+'>'+second+'</'+container+'></main>'
    if container=='li':content='<ul>'+content+'</ul>'
    rules=contract(named('houses',None,None,2 if complete else 1,required_fields=[
        {'id':'price','kind':'price'},{'id':'area','kind':'area'}]))
    rules['groups'][0].pop('start_pattern');rules['groups'][0].pop('end_pattern')
    case={'url':'https://example.test/catalog','task_kind':'body','min_content_chars':1,'composite_acceptance':rules}
    metrics=score_attempt(case,content.encode(),ok=True)
    assert metrics['composite_acceptance_pass'] is complete
    assert task_passed(True,metrics,'body') is complete


@pytest.mark.parametrize(
    ("content", "minimum", "expected"),
    [
        (
            '<main><div><p><a href="/item/1">First House</a><br>$100,000</p></div>'
            '<div><p>900 sqft<br><a href="/item/2">Second House</a></p></div></main>',
            1,
            False,
        ),
        (
            '<main><section><div><p><a href="/item/1">First House</a><br>$100,000</p></div></section>'
            '<section><div><p>900 sqft<br><a href="/item/2">Second House</a></p></div></section></main>',
            1,
            False,
        ),
        (
            '<article><ul><li><a href="/item/1">First House</a></li></ul>'
            '<p>$100,000</p><p>900 sqft</p></article>',
            1,
            True,
        ),
        (
            '<article><ul><li><a href="/item/1">First House</a></li>'
            '<li><a href="/item/2">Second House</a></li></ul>'
            '<p>$100,000</p><p>900 sqft</p></article>',
            2,
            False,
        ),
    ],
)
def test_html_record_owner_uses_effective_card_not_layout_container(
    content, minimum, expected
):
    from lexbench_webfetch.metrics import task_passed
    from lexbench_webfetch.scoring import score_attempt

    rules = contract(
        named(
            "houses",
            None,
            None,
            minimum,
            required_fields=[
                {"id": "price", "kind": "price"},
                {"id": "area", "kind": "area"},
            ],
        )
    )
    rules["groups"][0].pop("start_pattern")
    rules["groups"][0].pop("end_pattern")
    case = {
        "url": "https://example.test/catalog",
        "task_kind": "body",
        "min_content_chars": 1,
        "composite_acceptance": rules,
    }
    metrics = score_attempt(case, content.encode(), ok=True)
    assert metrics["composite_acceptance_pass"] is expected
    assert task_passed(True, metrics, "body") is expected


@pytest.mark.parametrize("complete_first", [False, True])
def test_duplicate_identity_is_validated_per_dom_record_before_deduplication(
    complete_first,
):
    from lexbench_webfetch.metrics import task_passed
    from lexbench_webfetch.scoring import score_attempt

    incomplete = '<article><a href="/item/1">First House</a><p>$100,000</p></article>'
    complete = (
        '<article><a href="/item/1">First House</a>'
        '<p>$100,000</p><p>900 sqft</p></article>'
    )
    content = complete + incomplete if complete_first else incomplete + complete
    rules = contract(
        named(
            "houses",
            None,
            None,
            1,
            required_fields=[
                {"id": "price", "kind": "price"},
                {"id": "area", "kind": "area"},
            ],
        )
    )
    rules["groups"][0].pop("start_pattern")
    rules["groups"][0].pop("end_pattern")
    case = {
        "url": "https://example.test/catalog",
        "task_kind": "body",
        "min_content_chars": 1,
        "composite_acceptance": rules,
    }
    metrics = score_attempt(case, content.encode(), ok=True)
    assert metrics["composite_acceptance_pass"] is True
    assert task_passed(True, metrics, "body") is True


def test_duplicate_incomplete_dom_records_cannot_combine_name_and_fields():
    from lexbench_webfetch.metrics import task_passed
    from lexbench_webfetch.scoring import score_attempt

    content = (
        '<article><a href="/item/1"><img alt="First House" src="/one.png"></a>'
        '<p>$100,000</p><p>900 sqft</p></article>'
        '<article><a href="/item/1">First House</a></article>'
    )
    rules = contract(
        named(
            "houses",
            None,
            None,
            1,
            required_fields=[
                {"id": "price", "kind": "price"},
                {"id": "area", "kind": "area"},
            ],
        )
    )
    rules["groups"][0].pop("start_pattern")
    rules["groups"][0].pop("end_pattern")
    case = {
        "url": "https://example.test/catalog",
        "task_kind": "body",
        "min_content_chars": 1,
        "composite_acceptance": rules,
    }
    metrics = score_attempt(case, content.encode(), ok=True)
    assert metrics["composite_acceptance_pass"] is False
    assert task_passed(True, metrics, "body") is False


@pytest.mark.parametrize('own_label', ['', 'Own name'])
@pytest.mark.parametrize('nested', [False, True])
def test_public_linked_table_preserves_owning_link(own_label, nested):
    from lexbench_webfetch.metrics import task_passed
    from lexbench_webfetch.scoring import score_attempt
    from lexbench_webfetch.structured_tables import table_text_surface

    inner = '<table><tr><td>Unrelated</td></tr></table>' if nested else ''
    content = ('<table><tr><th>Name</th></tr><tr><td><a href="/entry/1">'
               + own_label + inner + '</a></td></tr></table>')
    rules = contract({
        'id': 'entries', 'kind': 'linked_table', 'minimum_records': 1,
        'hosts': ['example.test'], 'entry_path': r'/entry/\d+',
        'required_columns': [{'id': 'name', 'kind': 'text', 'aliases': ['Name']}],
        'identity_column': 'name', 'link_column': 'name',
    })
    case = {'url': 'https://example.test/catalog', 'task_kind': 'body',
            'min_content_chars': 1, 'composite_acceptance': rules}
    metrics = score_attempt(case, content.encode(), ok=True)
    assert task_passed(True, metrics, 'body') is bool(own_label)
    surface = table_text_surface(content)
    if own_label:
        assert '[Own name](/entry/1)' in surface
    if nested:
        assert '| Unrelated |' in surface
        assert '[Unrelated](/entry/1)' not in surface
