import json

import pytest
from bs4 import BeautifulSoup

from harness.web_search.comparison import acceptance, comparison_data, outcome_relation, render_comparison


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
    assert acceptance(p["cases"][1], rows[3], "answer") is True
    assert acceptance(p["cases"][2], rows[6]) is None
    assert acceptance(p["cases"][0], None) is None
    assert acceptance(p["cases"][0], rows[0], "answer") is None


@pytest.mark.parametrize("base,other,expected", [
    (True,[True,True],"all_pass"), (False,[False,False],"all_fail"),
    (True,[False,False],"baseline_only"), (False,[True,True],"comparison_only"),
    (True,[True,False],"mixed"), (False,[True,False],"mixed"),
    (None,[False],"unknown"), (True,[None],"unknown"), (False,[],"unknown")])
def test_relations_do_not_hide_disagreement_or_unknown(base,other,expected):
    assert outcome_relation(base,other) == expected


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
    assert 'const size=3' in page


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
    assert "上方的题库、题目、检查项目、方法和命中状态会同步筛选这里" in page


def test_declared_population_filters_are_shared_and_exact():
    p, rows = matrix()
    p["comparison_populations"] = [
        {"id": "overall", "label": {"en": "Overall", "zh": "总体"}, "case_ids": ["target", "fact", "probe"]},
        {"id": "factual", "label": {"en": "Factual", "zh": "事实"}, "case_ids": ["fact"]},
        {"id": "reference", "label": {"en": "Reference", "zh": "参考"}, "case_ids": ["target"]},
    ]
    page = render_comparison(p, rows, True)
    dom = BeautifulSoup(page, "html.parser")
    assert [o.text for o in dom.select("[data-output-cohort] option")] == ["总体 · 3", "事实 · 1", "参考 · 1"]
    assert len(dom.select("[data-output-cohort]")) == 1
    assert page.index("data-output-cohort") < page.index("<h2>")
    assert dom.select_one("[data-relation-prev]") and dom.select_one("[data-relation-next]")
    data = json.loads(dom.select_one("[data-comparison-data]").string)
    assert data["populations"][2]["case_ids"] == ["target"]
    p["comparison_populations"][2]["case_ids"] = ["missing"]
    with pytest.raises(ValueError, match="unknown tasks"):
        comparison_data(p, rows)


def test_source_home_directory_preview_preserves_original():
    from harness.web_search.comparison import _preview
    source = 'Traceback File /Users/example/project/file.py:4 error'
    assert _preview(source, 320) == 'Traceback File [source path] error'
    assert '/Users/example/' in source
