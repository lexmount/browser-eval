"""Output-type isolation and evidence-backed field admission."""

from copy import deepcopy

import pytest

from harness.web_search.adapters import adapter_for
from harness.web_search.core import normalize_response, normalized_answer, request_body
from lexbench_websearch.outputs import levels, project_results, project_rows, qualify_field


def fields(*, extra=False, answer=False, body=False, preview=True):
    return dict(
        primary_preview="snippet" if preview else None,
        extra_text=["summary"] if extra else [],
        answer="answer" if answer else None,
        page_body="content" if body else None,
    )


def test_roles_define_levels_independently_of_length_and_page_bodies():
    item = dict(
        url="https://example.test/page",
        title="Title",
        snippet="P" * 5000,
        summary="Extra original text",
        content="Full page body",
        answer="Generated answer",
    )
    original = deepcopy(item)
    assert levels(fields(body=True)) == ["l1"]
    assert levels(fields(extra=True, answer=True, body=True)) == ["l1", "l2", "l3"]
    l1 = project_results([item], fields(extra=True, body=True), "l1")[0]
    assert l1 == dict(url=item["url"], title="Title", snippet="P" * 5000)
    l2 = project_results([item], fields(extra=True, body=True), "l2")[0]
    assert l2 == dict(url=item["url"], title="Title", snippet="Extra original text")
    assert project_results([item], fields(preview=False), "l1")[0]["snippet"] == ""
    assert item == original
    with pytest.raises(ValueError):
        levels(dict(fields(), primary_preview="summary"))
    with pytest.raises(ValueError):
        levels(dict(fields(), extra_text=["content"]))


def test_level_participation_keeps_empty_results_and_requires_full_populations():
    profile = dict(
        cases=[dict(id="a"), dict(id="b")],
        protocol={},
        methods=[
            dict(id="basic", output_fields=fields()),
            dict(id="extra", output_fields=fields(extra=True)),
        ],
    )
    rows = [
        dict(case_id=c["id"], method=m["id"], results=[])
        for c in profile["cases"]
        for m in profile["methods"]
    ]
    rows[-1]["results"] = [dict(url="https://example.test/document", summary="Extra text")]
    receipt = {"extra": dict(fields=["summary"], qualified=True)}
    selected, values = project_rows(profile, rows, "l2", qualified_fields=receipt)
    assert [m["id"] for m in selected["methods"]] == ["extra"]
    assert len(values) == 2 and values[0]["results"] == [] and values[1]["results"]
    with pytest.raises(ValueError, match="delivered nothing"):
        project_rows(
            profile, [dict(row, results=[]) for row in rows], "l2", qualified_fields=receipt
        )
    with pytest.raises(ValueError, match="complete"):
        project_rows(profile, rows[:-1], "l2", qualified_fields=receipt)
    with pytest.raises(ValueError, match="qualification"):
        project_rows(profile, rows, "l2")
    assert (
        project_rows(
            profile,
            rows,
            "l2",
            qualified_fields={"extra": dict(fields=["summary"], qualified=False)},
        )[1]
        == []
    )


def test_level_packets_hide_methods_and_ranks_and_reject_cross_level_fields():
    from harness.web_search.relevance import judge_input
    from lexbench_websearch.relevance import candidate_packet, score
    from lexbench_websearch.scoring import evaluate

    profile = dict(
        cases=[dict(id="query", query="Find the document", family="web")],
        methods=[dict(id="vendor", output_fields=fields(extra=True))],
        protocol=dict(top_k=5),
    )
    rows = [
        dict(
            case_id="query",
            method="vendor",
            results=[
                dict(
                    url="https://example.test/document",
                    rank=3,
                    title="Title",
                    snippet="Preview",
                    summary="Extra text",
                    content="Full body",
                )
            ],
        )
    ]
    receipts = {"vendor": dict(fields=["summary"], qualified=True)}
    ids = []
    for level, text in [("l1", "Preview"), ("l2", "Extra text")]:
        selected, values = project_rows(profile, rows, level, qualified_fields=receipts)
        packet, rankings = candidate_packet(selected, values)
        blind = judge_input(packet["blind_candidate_packets"][0])
        candidate = blind["candidates"][0]
        ids.append(candidate["candidate_id"])
        assert candidate["previews"] == [dict(title="Title", snippet=text)]
        if level == "l1":
            assert candidate["url"] == "https://example.test/document"
        else:
            assert "url" not in candidate
        assert "method" not in candidate and "rank" not in candidate
        labels = {"query": {candidate["candidate_id"]: 3}}
        assert score(selected, values, packet["plan"], labels)[0]["ranked_grades"] == [3]
        for forbidden in ("summary", "content", "answer"):
            leaked = deepcopy(values)
            leaked[0]["results"][0][forbidden] = "Leaked text"
            with pytest.raises(ValueError, match="Cross-level"):
                candidate_packet(selected, leaked)
    assert ids[0] != ids[1]
    case = dict(
        reference_answer="answer",
        reference_urls=["https://example.test/document"],
        evaluation_level="l2",
    )
    metrics = evaluate(
        case, [dict(url="https://example.test/document", title="answer", snippet="No cue")]
    )
    assert metrics["answer_in_extra_text_5"] == 0
    assert not any(key.startswith("labeled_source_") for key in metrics)


def test_original_text_admission_has_positive_negative_and_missing_evidence_controls():
    text = "This is an independently published paragraph describing a source document."
    samples = [
        dict(
            url=f"https://example.test/{i}",
            text=text,
            page_text="Page heading. " + text + " Footer.",
            observation_sha256="a" * 64,
        )
        for i in range(50)
    ]
    assert qualify_field(samples)["qualified"] is True
    generated = [
        dict(s, text="An invented paraphrase with different wording that is not on this page.")
        for s in samples
    ]
    assert qualify_field(generated)["qualified"] is False
    with pytest.raises(ValueError, match="complete"):
        qualify_field(samples[:-1])
    with pytest.raises(ValueError, match="Missing"):
        qualify_field([dict(samples[0], page_text=""), *samples[1:]])


@pytest.mark.parametrize(
    "adapter,options,expected,body",
    [
        ("lexmount", {}, ["l1"], False),
        ("serper", {}, ["l1"], False),
        ("brightdata", {}, ["l1"], False),
        ("browserbase", {}, ["l1"], False),
        ("tencent-wsa", {"edition": "lite"}, ["l1"], False),
        ("tencent-wsa", {"edition": "standard"}, ["l1", "l2"], False),
        ("tencent-wsa", {"edition": "flagship"}, ["l1", "l2"], False),
        ("deepseek", {"need_content": False}, ["l1", "l2"], False),
        ("deepseek", {"need_content": True}, ["l1", "l2"], True),
        ("bocha", {"summary": False}, ["l1"], False),
        ("bocha", {"summary": True}, ["l1", "l2"], False),
        ("bocha-ai", {"answer": True}, ["l1", "l2", "l3"], False),
        ("bocha-ai", {"answer": False}, ["l1", "l2"], False),
    ],
)
def test_adapter_capabilities_follow_native_fields_and_request_options(
    adapter, options, expected, body
):
    mapping = adapter_for(adapter).field_map(dict(output_options=options))
    assert levels(mapping) == expected
    assert bool(mapping["page_body"]) is body


def test_ai_search_keeps_webpage_order_and_answer_separate_from_followups():
    import json

    payload = dict(
        code=200,
        messages=[
            dict(
                type="source",
                content_type="webpage",
                content=json.dumps(
                    {
                        "value": [
                            dict(
                                id=7,
                                name="Document",
                                url="https://example.test/page",
                                snippet="Preview",
                                summary="Original excerpt",
                            )
                        ]
                    }
                ),
            ),
            dict(type="source", content_type="image", content="{}"),
            dict(type="answer", content_type="text", content="The answer [引用:7]"),
            dict(type="follow_up", content_type="text", content="A follow-up question"),
        ],
    )
    method = dict(adapter="bocha-ai", output_options=dict(answer=True, stream=False))
    protocol = dict(top_k=5, normalize_top_k=True, result_url_resolution="request-url/2")
    results = normalize_response(payload, method, protocol)
    assert results[0]["snippet"] == "Preview" and results[0]["summary"] == "Original excerpt"
    assert results[0]["citation_id"] == 7
    assert normalized_answer(payload, method) == "The answer [引用:7]"
    request = request_body(
        "bocha-ai",
        dict(query="site:example.test guide", family="site", domains=["example.test"]),
        protocol,
        method["output_options"],
    )
    assert request["include"] == "example.test" and request["count"] == 5
    assert request["answer"] is True and request["stream"] is False
    with pytest.raises(ValueError):
        normalize_response(dict(payload, code=403), method, protocol)
    payload["messages"][0]["content"] = json.dumps(
        {"value": [None, dict(id=7, url="https://example.test/page")]}
    )
    results = normalize_response(payload, method, protocol)
    assert results[0]["invalid_slot"] and results[1]["rank"] == 2


@pytest.mark.parametrize("adapter", ["serper", "brightdata"])
def test_query_language_policy_preserves_query_without_forcing_foreign_locale(adapter):
    from urllib.parse import parse_qs, urlsplit

    options = dict(gl="us", hl="en", **({"zone": "fixture"} if adapter == "brightdata" else {}))
    protocol = dict(top_k=5, locale_policy="query-language/1")

    def parameters(language, policy=protocol):
        result = request_body(adapter, dict(query="中文原问题", language=language), policy, options)
        return parse_qs(urlsplit(result["url"]).query) if adapter == "brightdata" else result

    chinese = parameters("zh-CN")
    assert "gl" not in chinese and "hl" not in chinese
    assert chinese["q"] in ("中文原问题", ["中文原问题"])
    assert "gl" in parameters("en") and "hl" in parameters("en")
    assert "gl" in parameters("zh", dict(top_k=5))
