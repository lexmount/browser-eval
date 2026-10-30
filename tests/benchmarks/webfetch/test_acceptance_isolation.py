"""Task acceptance must come from delivered content and current file evidence."""
import pytest

from lexbench_webfetch.metrics import summarize, task_passed
from lexbench_webfetch.scoring import score_attempt


@pytest.mark.parametrize("literal,other", [
    ("Home", "homepage"), ("Live", "deliver"), ("News", "newsletter"),
    ("Print", "blueprint"), ("url", "curl"), ("café", "caféteria"),
    ("Clone", "Cloned"), ("stats", "statsmodels"),
    ("stats", "αstats"), ("stats", "statsя"),
    ("Αθήνα", "Αθήνας"), ("новости", "новостик"),
])
@pytest.mark.parametrize("gate", ["fact", "marker", "anchor"])
def test_whole_word_requirements_reject_longer_words(literal, other, gate):
    case = {"url": "https://example.test/article", "expected_any": []}
    if gate == "fact":
        case["required_facts"] = [{"id": "answer", "alternatives": [literal]}]
    elif gate == "marker":
        case["expected_any"] = [literal]
    else:
        case["acceptance_contract"] = {"version": 1, "schema": "body_text",
            "minimum_records": 0,
            "stable_text_anchors": [{"id": "answer", "alternatives": [literal]}]}
    for answer, passed in [(other, False), (f"({literal})", True)]:
        metrics = score_attempt(case, answer.encode(), ok=True)
        assert task_passed(True, metrics) is passed


@pytest.mark.parametrize("body,passed", [(b"unrelated text", False),
    (b"Install with widget --start.", True), (b"", False)])
def test_document_title_cannot_supply_a_required_body_fact(body, passed):
    case = {"url": "https://example.test/article", "expected_any": [],
        "required_facts": [{"id": "installation", "alternatives": ["widget --start"]}]}
    metrics = score_attempt(case, body, ok=True, title="widget --start")
    assert task_passed(True, metrics) is passed


@pytest.mark.parametrize("current_pass", [True, False])
def test_current_file_validation_supersedes_a_previous_verdict(current_pass):
    case = {"url": "https://example.test/file", "task_kind": "file"}
    metadata = {"file_evaluation": {"pass": current_pass}}
    fresh = score_attempt(case, b"file attached", ok=True, metadata=metadata)
    rescored = score_attempt(case, b"file attached", ok=True, metadata=metadata,
        previous_metrics={"raw_file_pass": not current_pass, "rule_review_reason": "obsolete"})
    assert rescored == fresh
    assert task_passed(True, rescored) is current_pass


def test_removed_derived_metrics_do_not_survive_rescoring():
    case = {"url": "https://example.test/article", "expected_any": ["Article"]}
    fresh = score_attempt(case, b"Article", ok=True)
    rescored = score_attempt(case, b"Article", ok=True, previous_metrics={
        "retired_acceptance_pass": True, "rule_review_reason": "obsolete",
        "composite_acceptance_pass": False, "ordered_tree_similarity": 1.0})
    assert rescored == fresh


def test_a_legacy_file_verdict_without_current_file_evidence_cannot_certify_a_rescore():
    case = {"url": "https://example.test/file", "task_kind": "file"}
    metrics = score_attempt(case, b"file attached", ok=True,
        previous_metrics={"raw_file_pass": True, "raw_file_sha256": "legacy-hash"})
    assert "raw_file_pass" not in metrics
    assert metrics["raw_file_sha256"] == "legacy-hash"
    assert not task_passed(True, metrics)


def test_rescoring_preserves_collection_identity_and_deadline():
    case = {"url": "https://example.test/article"}
    collected = {"reference_sha256": "frozen-capture", "deadline_overrun": True,
        "deadline_budget_ms": 45000,
        "raw_file_sha256": "verified-file", "file_evidence": {"sha256": "verified-file"}}
    metrics = score_attempt(case, b"Article", ok=True, previous_metrics=collected)
    assert all(metrics[k] == v for k, v in collected.items())
    assert not task_passed(True, metrics)


def test_all_call_latency_includes_fast_success_slow_failure_and_timeout():
    rows = []
    for index, (elapsed, answer, ok) in enumerate([
        (10, b"Article", True), (1000, b"Wrong page", True), (45000, b"", False)]):
        case = {"url": "https://example.test/article", "expected_any": ["Article"]}
        rows.append({"provider": "browser", "provider_type": "local", "case_id": str(index),
            "case_name": "Article", "track": "web", "category": "article",
            "ok": ok, "elapsed_ms": elapsed,
            "metrics": score_attempt(case, answer, ok=ok)})
    metrics = summarize(rows)["providers"]["browser"]
    assert metrics["mean_content_ms"] == 10
    assert metrics["mean_all_attempts_ms"] == 15336.67
    assert metrics["all_attempts_p50_ms"] == 1000
    assert metrics["all_attempts_p95_ms"] == 45000


@pytest.mark.parametrize("track", ["web", "dynamic", "risk_control"])
@pytest.mark.parametrize("route", [None, "dynamic_selected_text", "another_interface"])
@pytest.mark.parametrize("destination,passed", [
    ("https://example.test/article", True), ("https://example.test/wrong", False), (None, False)])
def test_declared_page_identity_is_required_on_every_interface(track, route, destination, passed):
    case = {"url": "https://example.test/article", "track": track,
        "expected_any": ["Article"], "page_identity_required": True}
    if track == "dynamic":
        case.update(evaluation_url=case["url"], expected_final_url=case["url"],
                    target_language="en", target_region="US")
    if track == "risk_control":
        case["source_benchmark"] = {"risk_control": {"navigation_contract": {
            "requested_url": case["url"], "observed_final_url": case["url"]}}}
    metadata = {"document_language": "en"}
    if route:
        metadata["route"] = route
    metrics = score_attempt(case, b"Article", ok=True, metadata=metadata, final_url=destination)
    assert task_passed(True, metrics) is passed


@pytest.mark.parametrize("declaration", ["<![%draft;[\n<!ELEMENT note ANY>\n]]>",
    "<![UNRECOGNIZED[ XML source ]]>"])
def test_xml_declarations_cannot_crash_body_or_resource_scoring(declaration):
    body_case = {"url": "https://example.test/spec", "expected_any": ["Specification"]}
    raw = ("Specification\n" + declaration).encode()
    assert task_passed(True, score_attempt(body_case, raw, ok=True))
    resource_case = {**body_case, "task_kind": "resource", "content_score_applicable": False,
        "expected_resources": [{"id": "image", "urls": ["/target.png"]}]}
    for answer, passed in [(declaration + '\n<iframe srcdoc="example"></iframe>\n'
        + '[Target](/target.png)', True), (declaration + '\n<iframe></iframe>', False)]:
        metrics = score_attempt(resource_case, answer.encode(), ok=True)
        assert task_passed(True, metrics) is passed


@pytest.mark.parametrize("destination,passed", [
    ("https://example.test:443/article/#section", True),
    ("https://example.test/article?utm_source=link", True),
    ("https://example.test/article?q=another", False),
    ("https://example.test:8443/article", False),
    ("https://example.test:invalid/article", False)])
def test_identity_distinguishes_page_queries_and_ports(destination, passed):
    case = {"url": "https://example.test/article", "page_identity_required": True}
    assert task_passed(True, score_attempt(case, b"Article", ok=True, final_url=destination)) is passed


@pytest.mark.parametrize("mode,language,passed", [
    ("primary", "zh-TW", True), ("exact", "zh-TW", False), ("exact", "zh-CN", True)])
def test_tasks_can_require_the_complete_document_locale(mode, language, passed):
    case = {"url": "https://example.test/article", "track": "dynamic",
        "evaluation_url": "https://example.test/article", "expected_final_url": "https://example.test/article",
        "target_language": "zh-CN", "target_region": "CN", "page_identity_required": True,
        "document_language_match": mode}
    metrics = score_attempt(case, b"Article", ok=True, final_url=case["url"],
        metadata={"document_language": language})
    assert task_passed(True, metrics) is passed


@pytest.mark.parametrize("surface", ["native_json", "nested_metadata", "json_log_list"])
@pytest.mark.parametrize("delivered", [False, True])
def test_resource_answers_exclude_network_diagnostics(surface, delivered):
    import json
    target = "https://example.test/target.mp4"
    case = {"url": "https://example.test/page", "task_kind": "resource",
            "content_score_applicable": False,
            "expected_resources": [{"id": "video", "urls": [target]}]}
    diagnostics = {"network": [{"url": target, "final_url": target}]}
    if surface in {"native_json", "json_log_list"}:
        payload = {**diagnostics, "html": "<main>Article</main>"}
        if delivered:
            payload["resources"] = [target]
        if surface == "json_log_list" and not delivered:
            payload = diagnostics["network"]
        artifact, metadata = json.dumps(payload).encode(), {}
    else:
        artifact = b"No resource URL in the answer"
        metadata = {"extras": diagnostics}
        if delivered:
            metadata["videos"] = [target]
    metrics = score_attempt(case, artifact, ok=True, metadata=metadata)
    assert task_passed(True, metrics) is delivered
    assert bool(metrics["resource_hits"]) is delivered


@pytest.mark.parametrize("answer", ["caféteria", "décafé"])
def test_forbidden_latin_words_do_not_match_fragments(answer):
    case = {"url": "https://example.test/page", "forbidden_any": ["café"]}
    metrics = score_attempt(case, answer.encode(), ok=True)
    assert task_passed(True, metrics)
    assert not task_passed(True, score_attempt(case, b"A caf\xc3\xa9.", ok=True))


@pytest.mark.parametrize("kind", ["body", "resource", "file"])
def test_required_missing_identity_is_a_failure_in_the_full_population(kind):
    from lexbench_webfetch.metrics import task_measured
    case = {"url": "https://example.test/page", "page_identity_required": True, "task_kind": kind}
    metrics = score_attempt(case, b"Article", ok=True)
    assert task_measured(metrics)
    assert not task_passed(True, metrics)


@pytest.mark.parametrize("attribute", ["data-frame-url", "data-moli-frame-url"])
def test_visible_frame_relative_resources_use_the_declared_document_url(attribute):
    case = {"url": "https://example.test/page", "task_kind": "resource",
            "expected_resources": [{"id": "frame", "urls": ["https://frames.test/path/clip.mp4"]}]}
    artifact = (f'<iframe {attribute}="https://frames.test/path/index.html" '
                'srcdoc="&lt;video src=&quot;clip.mp4&quot;&gt;&lt;/video&gt;"></iframe>').encode()
    assert task_passed(True, score_attempt(case, artifact, ok=True))


@pytest.mark.parametrize("returned,passed", [("/target.mp4", True), ("/other.mp4", False)])
def test_a_returned_json_url_list_remains_a_resource_answer(returned, passed):
    import json
    case = {"url": "https://example.test/page", "task_kind": "resource",
            "expected_resources": [{"id": "video", "urls": ["/target.mp4"]}]}
    assert task_passed(True, score_attempt(case, json.dumps([returned]).encode(), ok=True)) is passed


@pytest.mark.parametrize("declaration", ["<?xml version=\"1.0\"?>",
    "<![UNRECOGNIZED[ XML source ]]>", "<![%draft;[<!ELEMENT note ANY>]]>"])
@pytest.mark.parametrize("position", ["before", "after"])
def test_xml_declarations_preserve_visible_frame_resource_answers(declaration, position):
    case = {"url": "https://example.test/spec", "task_kind": "resource",
            "expected_resources": [{"id": "image", "urls": ["/target.png"]}]}
    for filename, passed in [("target.png", True), ("other.png", False)]:
        frame = '<iframe srcdoc="&lt;img src=&quot;/' + filename + '&quot;&gt;"></iframe>'
        answer = declaration + frame if position == "before" else frame + declaration
        assert task_passed(True, score_attempt(case, answer.encode(), ok=True)) is passed


@pytest.mark.parametrize("literal,fragment", [("stats", "αstats"), ("stats", "statsя"),
    ("Αθήνα", "Αθήνας"), ("новости", "новостик")])
def test_forbidden_words_use_the_same_alphabetic_boundaries(literal, fragment):
    case = {"url": "https://example.test/article", "forbidden_any": [literal]}
    for answer, passed in [(fragment, True), ('(' + literal + ')', False)]:
        assert task_passed(True, score_attempt(case, answer.encode(), ok=True)) is passed
