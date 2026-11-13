"""Generated-answer correctness and citation evidence remain separate checks."""

import hashlib
import json

import pytest

from lexbench_websearch import answers


def test_official_grader_uses_hash_pinned_template_without_executing_upstream(monkeypatch):
    source = (
        b'raise RuntimeError("Must never execute")\n'
        b'GRADER_TEMPLATE = "Question: {question}\\nTarget: {target}\\n'
        b'Answer: {predicted_answer}".strip()\n'
    )
    monkeypatch.setitem(answers.SIMPLEQA_UPSTREAM, "sha256", hashlib.sha256(source).hexdigest())
    case = dict(id="q", query="Question text", reference_answer="Target text")
    messages = answers.factual_messages(case, "Predicted {answer}", source)
    assert messages == [
        dict(
            role="user",
            content="Question: Question text\nTarget: Target text\nAnswer: Predicted {answer}",
        )
    ]
    with pytest.raises(ValueError, match="hash mismatch"):
        answers.factual_messages(case, "Answer", source + b"\n")
    assert [answers.factual_grade(v) for v in ("A", " B\n", "C")] == [
        "CORRECT",
        "INCORRECT",
        "NOT_ATTEMPTED",
    ]
    for invalid in ("", "CORRECT", "The answer is A", "AB", "not attempted"):
        with pytest.raises(ValueError):
            answers.factual_grade(invalid)


def packet():
    case = dict(id="q", query="Who wrote the document?")
    row = dict(
        answer="Ada wrote it [引用:7]. More detail [引用:99]. See [source](http://www.example.test/doc).",
        results=[
            dict(
                url="https://example.test/doc",
                citation_id=7,
                title="Document",
                snippet="Ada wrote the document.",
                summary="Extra original text.",
                content="Full body must stay out",
            )
        ],
    )
    return answers.answer_packet(case, row)


def test_citations_resolve_native_ids_and_links_without_borrowing_page_body():
    value = packet()
    assert [c["in_results"] for c in value["citations"]] == [True, False, True]
    assert "content" not in value["citations"][0]["source"]
    user = json.loads(answers.answer_messages(value)[1]["content"])
    assert set(user) == {"case_id", "query", "answer"}
    assert json.loads(answers.answer_messages(value, citations=True)[1]["content"]) == value
    assert answers.citation_inventory("![image](https://example.test/doc)", []) == []
    inventory = answers.citation_inventory(
        "Claim [引用：7，8]",
        [
            dict(url="https://example.test/a", citation_id="7"),
            dict(url="https://example.test/b", citation_id=8),
        ],
    )
    assert len(inventory) == 2 and all(c["in_results"] for c in inventory)
    with pytest.raises(ValueError, match="different documents"):
        answers.citation_inventory(
            "Claim [引用:1]",
            [
                dict(url="https://example.test/a", citation_id=1),
                dict(url="https://example.test/b", citation_id=1),
            ],
        )


def test_citation_support_requires_complete_exact_evidence_from_the_cited_document():
    value = packet()
    judgments = [
        dict(
            citation_index=i,
            supported=False,
            claim=value["citations"][i]["claim_context"],
            reason="Not supported",
            evidence=None,
        )
        for i in range(3)
    ]
    judgments[0].update(
        supported=True,
        reason="The preview names the author",
        evidence=dict(field="snippet", quote="Ada wrote the document."),
    )
    assert answers.citation_grades(value, json.dumps(dict(judgments=judgments))) == judgments
    for altered in (
        judgments[:-1],
        [judgments[0]] * 3,
        [
            dict(judgments[0], evidence=dict(field="content", quote="Full body must stay out")),
            *judgments[1:],
        ],
        [
            dict(judgments[0], evidence=dict(field="summary", quote="Invented quote")),
            *judgments[1:],
        ],
        [judgments[0], dict(judgments[0], citation_index=1), judgments[2]],
    ):
        with pytest.raises(ValueError):
            answers.citation_grades(value, json.dumps(dict(judgments=altered)))


def test_scenario_answer_score_requires_answer_evidence_and_valid_grade():
    value = packet()
    judgment = dict(grade=3, reason="Names the author", evidence="Ada wrote it")
    assert answers.scenario_grade(value, json.dumps(judgment)) == judgment
    for altered in (
        dict(judgment, grade=True),
        dict(judgment, grade=4),
        dict(judgment, evidence="Invented answer"),
        dict(judgment, evidence=""),
    ):
        with pytest.raises(ValueError):
            answers.scenario_grade(value, json.dumps(altered))


def test_one_based_citations_resolve_zero_based_webpage_source_uris_without_changing_rank():
    results = [
        dict(url="https://source.test/a", citation_id="https://api.example.test/v1/#WebPages.0"),
        dict(url="https://source.test/b", citation_id="https://api.example.test/v1/#WebPages.3"),
    ]
    before = json.dumps(results, sort_keys=True)
    citations = answers.citation_inventory(
        "First [引用:1]; fourth [引用:4]; absent [引用:2].", results
    )
    assert [item["in_results"] for item in citations] == [True, True, False]
    assert [item["source"]["url"] if item["source"] else None for item in citations] == [
        "https://source.test/a",
        "https://source.test/b",
        None,
    ]
    assert json.dumps(results, sort_keys=True) == before


def test_citation_source_schemas_cannot_silently_resolve_to_different_documents():
    with pytest.raises(ValueError, match="different documents"):
        answers.citation_inventory(
            "A claim [引用:1]",
            [
                dict(
                    url="https://source.test/a",
                    citation_id="https://api.example.test/v1/#WebPages.0",
                ),
                dict(url="https://source.test/b", citation_id=1),
            ],
        )
    for malformed in [
        "WebPages.0",
        "https://api.example.test/#WebPages.-1",
        "https://api.example.test/#WebPages.00",
        "https://api.example.test/#other.0",
    ]:
        assert not answers.citation_inventory(
            "A claim [引用:1]", [dict(url="https://source.test/a", citation_id=malformed)]
        )[0]["in_results"]


@pytest.mark.parametrize("style", ["inline", "reference"])
def test_repeated_link_destinations_keep_each_occurrence_and_claim(style):
    if style == "inline":
        text = "Ada wrote it [first](https://test.example/a). Bob edited it [other](https://test.example/b). Eve wrote it [last](https://test.example/a)."
    else:
        text = "Ada wrote it [first][a]. Bob edited it [other][b]. Eve wrote it [last][a].\n\n[a]: https://test.example/a\n[b]: https://test.example/b"
    results = [
        dict(url="https://test.example/a", title="Authors", snippet="Ada wrote it"),
        dict(url="https://test.example/b", title="Editors", snippet="Bob edited it"),
    ]
    value = answers.answer_packet(dict(id="q", query="Who?"), dict(answer=text, results=results))
    citations = value["citations"]
    assert [c["source"]["url"] for c in citations] == [
        results[0]["url"],
        results[1]["url"],
        results[0]["url"],
    ]
    assert all(text[c["span"][0] : c["span"][1]] == c["marker"] for c in citations)
    assert citations[0]["span"][1] < citations[1]["span"][0] < citations[2]["span"][0]
    judgments = [
        dict(
            citation_index=i,
            supported=False,
            claim=c["claim_context"],
            reason="No support",
            evidence=None,
        )
        for i, c in enumerate(citations)
    ]
    assert answers.citation_grades(value, json.dumps(dict(judgments=judgments)))
    judgments[2].update(
        supported=True, claim="Ada wrote it", evidence=dict(field="snippet", quote="Ada wrote it")
    )
    with pytest.raises(ValueError, match="cited claim"):
        answers.citation_grades(value, json.dumps(dict(judgments=judgments)))


def test_explanatory_prose_is_optional_but_verdict_claim_and_source_quote_are_required():
    value = packet()
    judgments = [
        dict(citation_index=i, supported=False, claim=c["claim_context"], evidence=None)
        for i, c in enumerate(value["citations"])
    ]
    judgments[0].update(
        supported=True, evidence=dict(field="snippet", quote="Ada wrote the document.")
    )
    original = json.dumps(judgments, sort_keys=True)
    assert answers.citation_grades(value, json.dumps(dict(judgments=judgments))) == judgments
    assert json.dumps(judgments, sort_keys=True) == original
    for field in ("supported", "claim", "evidence"):
        changed = json.loads(original)
        del changed[0][field]
        with pytest.raises(ValueError):
            answers.citation_grades(value, json.dumps(dict(judgments=changed)))
    changed = json.loads(original)
    changed[0]["evidence"]["quote"] = "A quotation absent from the cited source"
    with pytest.raises(ValueError, match="exact quote"):
        answers.citation_grades(value, json.dumps(dict(judgments=changed)))
