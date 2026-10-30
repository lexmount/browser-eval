"""Public acceptance controls for record and prose boundary failures."""
import html
import json

import pytest

from lexbench_webfetch.body_coverage import body_requirements
from lexbench_webfetch.dynamic_lists import extract_named_entries
from lexbench_webfetch.evaluation import evaluate_response


def case(**fields):
    return {"id": "surface-control", "name": "Visible output", "category": "web",
            "url": "https://shop.example/catalog", "expected_any": [], **fields}


@pytest.mark.parametrize("answer,passed", [
    ("GET[/pets](/docs)List pets", True),
    ("GET /pets List pets", True),
    ("GET[/pets/extra](/docs)List pets", False),
    ("GET[/pets](/docs)List other things", False),
])
def test_joined_path_summary_uses_supported_record_boundaries(answer, passed):
    facts = [{"id": f"entry-1-{field}", "alternatives": [value]}
             for field, value in [("method", "GET"), ("path", "/pets"),
                                  ("summary", "List pets")]]
    task = case(required_facts=facts, ground_truth_main_content="GET /pets List pets")
    assert evaluate_response(task, answer.encode())["passed"] is passed


def test_malformed_caption_image_drops_only_that_candidate():
    contract = {"hosts": ["shop.example"], "entry_path": r"/models/[a-z]+", "minimum_entries": 1}
    broken = "[](/models/abc)\n![](http://[bad]/abc.png)\nAB123 Model"
    sibling = "[Visible model](/models/def)"
    assert extract_named_entries(broken + "\n\n" + sibling, contract, "https://shop.example/") == [
        {"url": "https://shop.example/models/def", "title": "Visible model"}]
    task = case(acceptance_contract={"version": 1, "schema": "named_link", "minimum_records": 1,
                                    "hosts": contract["hosts"], "entry_path": contract["entry_path"],
                                    "stable_text_anchors": []})
    assert evaluate_response(task, (broken + "\n\n" + sibling).encode())["passed"]
    assert not evaluate_response(task, broken.encode())["passed"]
    visible = broken.replace("http://[bad]", "https://images.example")
    assert extract_named_entries(visible, contract, "https://shop.example/")[0]["title"] == "AB123 Model"


@pytest.mark.parametrize("answer,passed", [
    ("[Widget](/product/1)\n\n[Details](/product/2) $20.00", False),
    ("[Widget](/product/1) $10.00\n\n[Details](/product/2) $20.00", True),
    ("[Widget](/product/1)\n[Details](/product/1) $10.00", True),
])
def test_unnamed_product_still_owns_its_price(answer, passed):
    task = case(acceptance_contract={
        "version": 1, "schema": "linked_product", "minimum_records": 1,
        "hosts": ["shop.example"], "entry_path": r"/product/\d+",
        "price_patterns": [r"\$\d+\.\d{2}"], "stable_text_anchors": []})
    assert evaluate_response(task, answer.encode())["passed"] is passed


@pytest.mark.parametrize("surface", ["html", "json", "frame", "markdown", "autolink"])
def test_html_or_markdown_link_has_one_decoded_resource_candidate(surface):
    url = "https://example.test/download?a=1&b=2"
    link = '<a href="' + html.escape(url) + '">Download</a>'
    if surface == "json":
        answer = json.dumps({"html": link})
    elif surface == "frame":
        answer = '<iframe srcdoc="' + html.escape(link, quote=True) + '"></iframe>'
    elif surface == "markdown":
        answer = '[Download](' + html.escape(url) + ')'
    elif surface == "autolink":
        answer = '<' + url + '>'
    else:
        answer = link
    task = case(task_kind="resource", content_score_applicable=False,
                expected_resources=[{"urls": [url]}])
    metrics = evaluate_response(task, answer.encode())["metrics"]
    assert metrics["resource_candidate_urls"] == [url]
    assert metrics["resource_precision"] == 1
    task["expected_resources"] = [{"urls": [html.escape(url)]}]
    assert not evaluate_response(task, answer.encode())["passed"]


@pytest.mark.parametrize("body", [False, True])
@pytest.mark.parametrize("answer,passed", [
    ("Secret required phrase", True),
    ("![Secret required phrase](image.png)", False),
    ("[![Secret required phrase](image.png)](/landing)", False),
    ("Intro\n<!-- Secret required phrase -->", False),
    ("`<!-- Secret required phrase -->`", True),
    ("```html\n<!-- Secret required phrase -->\n```", True),
    ("`![Secret required phrase](image.png)`", True),
])
def test_prose_facts_and_coverage_exclude_metadata_but_keep_literal_examples(body, answer, passed):
    fields = ({"body_requirements": body_requirements("Secret required phrase")}
              if body else {"required_facts": [{"alternatives": ["Secret required phrase"]}]})
    task = case(ground_truth_main_content="Secret required phrase", **fields)
    assert evaluate_response(task, answer.encode())["passed"] is passed


def test_explicit_resource_patterns_keep_declared_decoded_filename_equivalence():
    task = case(task_kind="resource", content_score_applicable=False,
                expected_resources=[{"urls": ["https://example.test/a%28b%29.png"],
                                     "url_patterns": [r"^https://example\.test/a\(b\)\.png\?size=\d+$"]}])
    assert evaluate_response(task, b"https://example.test/a%28b%29.png?size=640")["passed"]
    task["expected_resources"][0].pop("url_patterns")
    assert not evaluate_response(task, b"https://example.test/a%28b%29.png?size=640")["passed"]
