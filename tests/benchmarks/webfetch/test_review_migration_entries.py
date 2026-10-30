"""Record identity, visible ownership and resource URL boundary controls."""
import html
import json

import pytest

from lexbench_webfetch.dynamic_entry_matching import matching_entry_facts
from lexbench_webfetch.dynamic_lists import extract_linked_products, extract_named_entries
from lexbench_webfetch.evaluation import evaluate_response
from lexbench_webfetch.list_verification import verify_list
from lexbench_webfetch.metrics import fact_in_text, normalize_for_match


def entry_rules(**extra):
    return {"hosts": ["shop.example"], "entry_path": r"/item/\d+",
            "minimum_entries": 1, **extra}


@pytest.mark.parametrize("text,expected", [
    ("Hello byAlbert Tags: science", True),
    ("Hello by Albert Tags: science", True),
    ("Hello byAlberton Tags: science", False),
    ("Hello byAlbert Tags: unscience", False),
])
def test_declared_attached_field_labels_preserve_fact_boundaries(text, expected):
    facts = [{"id": f"entry-1-{field}", "alternatives": [value]}
             for field, value in [("quote", "Hello"), ("author", "Albert"),
                                  ("tags", "science")]]
    hits = matching_entry_facts(normalize_for_match(text), facts,
                                normalize_for_match, fact_in_text)
    assert all(hits.values()) is expected


@pytest.mark.parametrize("wrapper", [
    '<div hidden>{}</div>', '<template>{}</template>',
    '<div style="display: none">{}</div>', '<details><summary>More</summary>{}</details>',
])
@pytest.mark.parametrize("link", ['<a href="/item/1">First item</a>', '[First item](/item/1)'])
def test_hidden_records_cannot_satisfy_unscoped_list(wrapper, link):
    rules = entry_rules()
    assert extract_named_entries(wrapper.format(link), rules, "https://shop.example/") == []
    visible = extract_named_entries(link, rules, "https://shop.example/")
    assert len(visible) == 1
    products = {**rules, "price_patterns": [r"\$\d+\.\d{2}"]}
    assert extract_linked_products(wrapper.format(link + " $10.00"), products,
                                   "https://shop.example/") == []
    assert len(extract_linked_products(link + " $10.00", products,
                                      "https://shop.example/")) == 1


@pytest.mark.parametrize("label,expected", [
    ("![Widget](image.jpg)", False),
    ("![Thumbnail](image.jpg) Widget", True),
    ("Widget", True),
])
def test_product_name_requires_visible_text(label, expected):
    rules = entry_rules(entry_path=r"/product/\d+", price_patterns=[r"\$\d+\.\d{2}"])
    found = extract_linked_products(f"[{label}](/product/1) $10.00", rules,
                                   "https://shop.example/")
    assert bool(found) is expected


def test_product_links_use_declared_base_without_expanding_allowed_hosts():
    rules = entry_rules(entry_path=r"/product/\d+", price_patterns=[r"\$\d+\.\d{2}"],
                        link_base_url="https://shop.example/catalog/")
    good = extract_linked_products("[Widget](/product/1) $10.00", rules,
                                  "https://redirect.example/start")
    assert good == [{"url": "https://shop.example/product/1", "title": "Widget", "price": "$10.00"}]
    assert extract_linked_products("[Widget](https://other.example/product/1) $10.00", rules,
                                   "https://redirect.example/start") == []
    relative = {**rules, "entry_path": r"/catalog/product/\d+"}
    assert extract_linked_products("[Widget](product/1) $10.00", relative,
                                   "https://redirect.example/start")[0]["url"] == "https://shop.example/catalog/product/1"


def test_query_identity_is_shared_by_returned_records_and_reference_checks():
    rules = entry_rules(entry_path="/item", identity_query_keys=["id"], minimum_entries=2)
    reference = {"items": [{"url": f"https://shop.example/item?id={i}", "title": "Same name"}
                           for i in (1, 2)]}
    answer = "[Same name](/item?id=1)\n[Same name](/item?id=2)"
    result = verify_list(answer, rules, "https://shop.example/", reference)
    assert result["checked_entries"] == 2
    assert result["list_task_pass"]
    rules["minimum_entries"] = 1
    reference = {"items": [{"url": "https://shop.example/item?id=2", "title": "Same name"}],
                 "access_checks": [{"url": "https://shop.example/item?id=2", "restriction": True}]}
    result = verify_list("[Same name](/item?id=1)", rules, "https://shop.example/", reference)
    assert not result["list_task_pass"]
    assert not result["list_checks"][0]["access_restricted"]
    reference["items"].extend([
        {"url": "https://shop.example/item", "title": "Same name"},
        {"url": "/item?id=1", "title": "Same name"},
    ])
    reference["access_checks"].append({"url": "https://shop.example/item?id=", "restriction": True})
    assert not verify_list("[Same name](/item?id=1)", rules, "https://shop.example/",
                           reference)["list_task_pass"]


def resource_case(url):
    return {"id": "resource-boundary", "name": "Resource URL", "url": "https://example.test/page",
            "category": "resource", "task_kind": "resource", "content_score_applicable": False,
            "expected_resources": [{"id": "download", "urls": [url]}]}


@pytest.mark.parametrize("expected,returned,passed", [
    ("/a%2Fb.png", "/a/b.png", False),
    ("/a%2Fb.png", "/a%2fb.png", True),
    ("/a%5Fb.png", "/a_b.png", True),
    ("/a%3Fb.png", "/a?b.png", False),
    ("/caf%C3%A9.png", "/café.png", True),
    ("/a%28b%29.png", "/a(b).png", False),
])
def test_resource_paths_only_decode_unreserved_characters(expected, returned, passed):
    case = resource_case("https://example.test" + expected)
    assert evaluate_response(case, ("https://example.test" + returned).encode())["passed"] is passed


@pytest.mark.parametrize("surface", ["html", "json-html", "frame", "bare"])
def test_html_resource_attributes_decode_entities_but_bare_urls_are_literal(surface):
    url = "https://example.test/download?a=1&b=2"
    markup = '<a href="' + html.escape(url) + '">Download</a>'
    if surface == "html":
        response = markup
    elif surface == "json-html":
        response = json.dumps({"html": markup})
    elif surface == "frame":
        response = '<iframe srcdoc="' + html.escape(markup, quote=True) + '"></iframe>'
    else:
        response = html.escape(url)
    assert evaluate_response(resource_case(url), response.encode())["passed"] is (surface != "bare")
