import json

import pytest
from bs4 import BeautifulSoup

from harness.web_search.comparison import acceptance, comparison_data, render_comparison


def matrix():
    profile = {"methods": [{"id": x, "label": f"Provider {x}"} for x in "abc"],
               "cases": [{"id": "target", "query": "Find a page", "anchors": ["https://example.org/"]},
                         {"id": "fact", "query": "Who?", "reference_answer": "Ada", "reference_urls": ["https://example.org/ada"]},
                         {"id": "probe", "query": "Explore"}]}
    rows = [{"id": c["id"] + m, "case_id": c["id"], "method": m,
             "results": [{"url": "https://example.org/", "title": "Page", "snippet": "Useful text", "summary": "s" * 10000, "content": "c" * 10000}],
             "metrics": {"anchor_hit_5": int(m == "a") if c["id"] == "target" else None,
                         "labeled_source_hit_5": int(m != "a"), "answer_in_delivered_text_5": 1}}
            for c in profile["cases"] for m in "abc"]
    return profile, rows


def test_scoped_acceptance_keeps_unlabelled_and_missing_unknown():
    p, rows = matrix()
    assert acceptance(p["cases"][0], rows[0]) is True
    assert acceptance(p["cases"][0], rows[1]) is False
    assert acceptance(p["cases"][1], rows[3]) is False
    # Frozen metrics can be stale; factual checks use the delivered evidence.
    assert acceptance(p["cases"][1], rows[3], "answer") is False
    assert acceptance(p["cases"][2], rows[6]) is None
    assert acceptance(p["cases"][0], None) is None
    assert acceptance(p["cases"][0], rows[0], "answer") is None
    assert acceptance(p["cases"][0], rows[0], "primary") is True
    assert acceptance(p["cases"][1], rows[3], "primary") is False
    assert acceptance(p["cases"][2], rows[6], "primary") is None


def test_factual_explanation_follows_the_installed_answer_policy(monkeypatch):
    from lexbench_websearch import answer_rules
    from harness.web_search.report import factual_panel

    profile, rows = matrix()
    profile["protocol"] = {"top_k": 5}
    monkeypatch.setattr(answer_rules, "POLICY_NAME", "published-answers/99")
    case = next(case for case in profile["cases"] if "reference_answer" in case)
    case["answer_match_rule"] = answer_rules.POLICY_NAME
    assert "explicit aliases and allowed numeric ranges" in factual_panel(profile, rows, False)
    del case["answer_match_rule"]
    assert "requires consecutive whole tokens" in factual_panel(profile, rows, False)


def test_retrieval_report_defaults_factual_comparison_to_source_not_answer_text():
    from harness.web_search.report import render_result_comparison
    p, rows = matrix()
    rows[3]["results"][0]["snippet"] = "Ada"
    rows[4]["results"][0]["url"] = "https://example.org/ada"
    legacy = comparison_data(p, rows)
    retrieval = comparison_data(p, rows, primary_factual_metric="source")
    assert legacy["cases"][1]["outputs"]["a"]["primary"] is True
    assert retrieval["cases"][1]["outputs"]["a"]["primary"] is False
    assert legacy["cases"][1]["outputs"]["b"]["primary"] is False
    assert retrieval["cases"][1]["outputs"]["b"]["primary"] is True
    html = render_result_comparison(p, rows, True, content={"summary_metrics": "retrieval"})
    embedded = json.loads(BeautifulSoup(html, "html.parser").select_one('[data-comparison-data]').string)
    assert embedded["primary_factual_metric"] == "source"
    assert embedded["cases"][1]["outputs"]["b"]["primary"] is True



def test_output_previews_are_bounded_and_keep_missing_and_empty_distinct():
    p,rows = matrix()
    rows[0]["results"] = []
    rows = rows[:-1]
    data = comparison_data(p,rows)
    assert data["cases"][0]["outputs"]["a"]["count"] == 0
    assert data["cases"][2]["outputs"]["c"] is None
    out = data["cases"][0]["outputs"]["b"]
    assert len(out["items"][0]["content"]) == 480
    assert len(out["items"][0]["summary"]) == 10000
    assert out["detail"] == "details/targetb.html"
    with pytest.raises(ValueError, match="one logical"):
        comparison_data(p,rows + [rows[0]])


def test_safe_serialization_registry_labels_links_and_pagination():
    p,rows = matrix()
    p["cases"][0]["query"] = '</script><img src=x onerror=alert(1)>'
    rows[0]["results"][0]["url"] = 'javascript:alert(1)'
    page = render_comparison(p,rows,False,lambda r: f'detail.html?id={r["id"]}')
    dom = BeautifulSoup(page,"html.parser")
    assert not dom.find("img")
    data = json.loads(dom.select_one('[data-comparison-data]').string)
    assert data["cases"][0]["query"] == p["cases"][0]["query"]
    assert data["cases"][0]["outputs"]["a"]["items"][0]["url"] == ''
    assert data["cases"][0]["outputs"]["a"]["detail"] == 'detail.html?id=targeta'
    assert [o.text for o in dom.select('[data-compare-baseline] option')] == ['Provider a','Provider b','Provider c']
    assert dom.select_one('[data-output-prev]') and dom.select_one('[data-output-next]')
    assert not dom.select_one('[data-output-pages]').contents
    assert 'slice(page*size,(page+1)*size)' in page
    assert 'matches.slice(page*size,(page+1)*size)' in page
    assert 'const size=1' in page


def test_result_view_marks_exact_source_and_answer_evidence_without_a_judge():
    p, rows = matrix()
    source = comparison_data(p, rows)["cases"][0]["outputs"]["a"]["items"][0]
    answer = comparison_data(p, rows)["cases"][1]["outputs"]["a"]["items"][0]
    assert source["source_match"] is True
    assert source["answer_fields"] == {"title": False, "snippet": False, "summary": False, "content": False}
    rows[3]["results"][0]["summary"] = "Ada"
    answer = comparison_data(p, rows)["cases"][1]["outputs"]["a"]["items"][0]
    assert answer["answer_fields"]["summary"] is True
    assert answer["source_match"] is False


def test_reader_labels_state_what_was_found_and_full_summary_scope():
    p, rows = matrix()
    page = render_comparison(p, rows, True)
    assert "符合所选检查" not in page
    assert "不符合所选检查" not in page
    assert "完整长摘要" in page
    assert "上方筛选条件同步控制这里" in page


def test_benchmark_and_task_type_filters_are_separate():
    p, rows = matrix()
    p["cases"][0]["domains"] = ["example.org"]
    p["comparison_populations"] = [
        {"id": "overall", "label": {"en": "Overall", "zh": "总体"}, "case_ids": ["target", "fact", "probe"]},
        {"id": "factual", "label": {"en": "Factual", "zh": "事实"}, "case_ids": ["fact"]},
        {"id": "reference", "label": {"en": "Reference", "zh": "参考"}, "case_ids": ["target"]},
    ]
    page = render_comparison(p, rows, True)
    dom = BeautifulSoup(page, "html.parser")
    assert [o.text for o in dom.select("[data-output-benchmark] option")] == [
        "全部响应 · 3", "自建场景集 · 2", "公开事实题 · 1"
    ]
    assert [o.text for o in dom.select("[data-output-check] option")] == [
        "全部题型", "目标网页发现", "指定网站约束", "通用网页查询", "事实证据"
    ]
    assert page.index("data-output-benchmark") < page.index("<h2>")
    assert dom.select_one("[data-relation-prev]") and dom.select_one("[data-relation-next]")
    data = json.loads(dom.select_one("[data-comparison-data]").string)
    assert data["populations"][2]["case_ids"] == ["target"]
    assert data["cases"][0]["benchmark"] == "custom"
    assert data["cases"][0]["check_types"] == ["target", "site"]
    assert data["cases"][1]["benchmark"] == "factual"
    p["comparison_populations"][2]["case_ids"] = ["missing"]
    with pytest.raises(ValueError, match="unknown tasks"):
        comparison_data(p, rows)


def test_source_home_directory_preview_preserves_original():
    from harness.web_search.comparison import _preview
    source = 'Traceback File /Users/example/project/file.py:4 error'
    assert _preview(source, 320) == 'Traceback File [source path] error'
    assert '/Users/example/' in source


def test_reader_can_inspect_reference_answer_urls_and_site_without_opening_raw_json():
    p, rows = matrix()
    p["cases"][0]["domains"] = ["example.org"]
    data = comparison_data(p, rows)
    assert data["cases"][0]["criteria"]["urls"] == ["https://example.org/"]
    assert data["cases"][0]["criteria"]["domains"] == ["example.org"]
    assert data["cases"][1]["criteria"]["answer"] == "Ada"
    assert data["cases"][1]["criteria"]["urls"] == ["https://example.org/ada"]
    assert data["cases"][0]["outputs"]["a"]["items"][0]["site_match"] is True


def test_empty_result_is_site_failure_only_when_query_restricts_sites():
    row = {"results": [], "metrics": {"result_count": 0}}
    assert acceptance({"domains": ["example.org"]}, row, "site") is False
    assert acceptance({}, row, "site") is None
    assert acceptance({}, row, "primary") is None
    assert acceptance({}, row, "availability") is False


def test_human_relevance_is_the_primary_quality_check_when_complete_labels_exist():
    import hashlib
    from lexbench_websearch.scoring import canonical_url
    p, rows = matrix()
    case = p["cases"][0]
    cid = hashlib.sha256((case["id"] + "\0" + canonical_url(rows[0]["results"][0]["url"])).encode()).hexdigest()[:12]
    evidence = {"labels": {case["id"]: {cid: 2}}}
    data = comparison_data(p, rows, relevance=evidence)
    # Provider b misses the preselected URL metric in this fixture, but its page
    # has the same frozen human relevance label as the other providers.
    out = data["cases"][0]["outputs"]["b"]
    assert out["source"] is False
    assert out["primary"] is True
    assert out["quality"] is True
    assert out["items"][0]["grade"] == 2
    assert data["cases"][2]["outputs"]["a"]["quality"] is None


def test_answer_beyond_body_excerpt_does_not_highlight_unmatched_preview():
    p, rows = matrix()
    row = rows[3]
    row["results"][0]["content"] = "unrelated text " * 100 + " Ada"
    item = comparison_data(p, rows)["cases"][1]["outputs"]["a"]["items"][0]
    assert item["answer_fields"]["content"] is False
    assert item["answer_outside_preview"] is True
    assert "Ada" not in item["content"]


@pytest.mark.parametrize('zh', [True, False])
def test_one_query_per_output_page_preserves_all_providers_and_full_text(zh):
    from harness.reporting.visual import browser_executable
    from playwright.sync_api import sync_playwright
    profile, rows = matrix()
    with sync_playwright() as engine:
        browser = engine.chromium.launch(executable_path=browser_executable(), headless=True)
        try:
            page = browser.new_page()
            page.set_content(render_comparison(profile, rows, zh))
            assert page.locator('.compare-task').count() == 1
            assert page.locator('.compare-task h3').inner_text() == 'Find a page'
            assert page.locator('.compare-task .compare-method').count() == 3
            assert 's' * 10000 in page.locator('.compare-task').text_content()
            page.locator('[data-output-next]').click()
            assert page.locator('.compare-task').count() == 1
            assert page.locator('.compare-task h3').inner_text() == 'Explore'
            assert page.locator('.compare-task .compare-method').count() == 3
            page.locator('[data-output-prev]').click()
            assert page.locator('.compare-task h3').inner_text() == 'Find a page'
        finally:
            browser.close()


@pytest.mark.parametrize("zh", [False, True])
def test_output_notice_describes_only_delivered_fields(zh):
    profile, rows = matrix()
    for row in rows:
        for item in row["results"]:
            item["summary"] = item["content"] = ""
    def notice():
        dom = BeautifulSoup(render_comparison(profile, rows, zh), "html.parser")
        return dom.select(".compare-notice")[-1].get_text()
    assert ("标题、摘要" if zh else "returned titles and snippets") in notice()
    assert ("正文" if zh else "Page bodies") not in notice()
    assert ("长摘要" if zh else "Page summaries") not in notice()
    rows[0]["results"][0]["summary"] = "An API-provided summary"
    assert ("长摘要完整显示" if zh else "summaries are shown in full") in notice()
    assert ("正文" if zh else "Page bodies") not in notice()
    rows[0]["results"][0]["content"] = "An API-provided page body"
    assert ("网页正文显示节选" if zh else "bodies are shown as excerpts") in notice()
