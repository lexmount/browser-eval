"""Paired controls for diagnostic mutation completion and official scoring."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode

import pytest
from bs4 import BeautifulSoup

from harness.browser_use.runner import _configure_benchmark

ROOT = Path(__file__).resolve().parents[2]
CANDIDATES = (
    ROOT / "evaluation/trajectory-replay/webarena-verified/mutation-completion-candidates.json"
)
TRAJECTORIES = ROOT / "evaluation/trajectory-replay/webarena-verified/trajectories.json"
REVIEW = ROOT / "evaluation/trajectory-replay/webarena-verified/mutation-completion-review.json"


@pytest.fixture(scope="module")
def candidates() -> dict[int, dict]:
    payload = json.loads(CANDIDATES.read_text(encoding="utf-8"))
    assert payload["schema"] == "webarena_mutation_completion_candidates/1"
    assert payload["official_evaluator"]["required_score"] == 1.0
    return {int(row["task_id"]): row for row in payload["rules"]}


def test_candidates_bind_frozen_actions_and_official_task_contract(candidates):
    trajectories = {
        int(row["task_id"]): row
        for row in json.loads(TRAJECTORIES.read_text(encoding="utf-8"))["trajectories"]
    }
    benchmark = _configure_benchmark(hostname="192.168.5.2")
    assert set(candidates) == {399, 404, 415, 418, 441}
    for task_id, candidate in candidates.items():
        source = trajectories[task_id]
        assert candidate["action_count"] == len(source["tool_calls"])
        assert source["evaluation_input"]["status"] == "SUCCESS"
        task = benchmark.get_task(task_id)
        assert task.revision == 2
        official = next(row for row in task.eval if row.evaluator == "NetworkEventEvaluator")
        expected = official.expected.model_dump(exclude_none=True)
        event = candidate["official_network_event"]
        assert expected["http_method"] == event["method"]
        assert expected["url"].endswith(event["url_suffix"])
        assert expected.get("post_data") == event["post_data"]
        if "response_status" in event:
            assert expected["response_status"] == event["response_status"]
        assert candidate["observer"]["kind"].startswith(("same_origin_get", "pending_"))
        assert "official evaluator" in candidate["qualification"]


def _observed(candidate: dict, body: str) -> bool:
    """Exercise the proposed read-only predicates on synthetic response bodies."""
    observer = candidate["observer"]
    kind = observer["kind"]
    if kind == "same_origin_get_json":
        return json.loads(body).get(observer["json_key"]) == observer["equals"]
    if kind == "same_origin_get_text":
        soup = BeautifulSoup(body, "html.parser")
        titles = [node.get_text(strip=True) for node in soup.select("title")]
        return titles == [observer["require_html_title"]] and _normalized(body) == _normalized(
            _frozen_fill(candidate)
        )
    if kind == "same_origin_get_html":
        soup = BeautifulSoup(body, "html.parser")
        if candidate["task_id"] == 399:
            fields = soup.select(observer["selector"])
            return len(fields) == 1 and fields[0].get_text() == observer["equals"]
        notes = []
        for node in soup.select(observer["within"]):
            author = node.select_one(f'a[href="{observer["require_author_href"]}"]')
            permalink = node.select_one('a[href^="#note_"]')
            paragraphs = node.select("p")
            if (
                author
                and permalink
                and paragraphs
                and observer["paragraph_contains"]
                in " ".join(p.get_text(" ", strip=True) for p in paragraphs)
            ):
                import re

                if re.fullmatch(observer["require_permalink_pattern"], permalink.get("href", "")):
                    notes.append(node)
        return len(notes) == 1
    return False


def _normalized(value: str) -> str:
    return value.replace("\r\n", "\n")


def _frozen_fill(candidate: dict) -> str:
    row = next(
        row
        for row in json.loads(TRAJECTORIES.read_text(encoding="utf-8"))["trajectories"]
        if row["task_id"] == candidate["task_id"]
    )
    return row["tool_calls"][candidate["observer"]["expected_from_tool_call_index"]]["text"]


def _pre_commit_observed(candidate: dict, actual_value: str) -> bool:
    observer = candidate["pre_commit_observer"]
    expected = _frozen_fill(candidate)
    return hashlib.sha256(expected.encode()).hexdigest() == observer[
        "expected_text_sha256"
    ] and _normalized(actual_value) == _normalized(expected)


@pytest.mark.parametrize(
    ("task_id", "positive", "negative"),
    [
        (
            399,
            '<textarea name="user_biography[biography]">I am a robot</textarea>',
            '<textarea name="user_biography[biography]">I am not a robot</textarea>',
        ),
        (
            415,
            '<main><li><a href="/byteblaze">Byte Blaze</a>'
            '<a href="#note_42">Permalink</a><p>@davepgreene</p></li></main>',
            '<main><li><a href="/byteblaze">Byte Blaze</a><p>@davepgreene</p></li></main>',
        ),
        (418, '{"availability":"busy"}', '{"availability":"available"}'),
    ],
)
def test_read_only_observer_requires_persisted_state(
    candidates, task_id: int, positive: str, negative: str
):
    assert _observed(candidates[task_id], positive)
    assert not _observed(candidates[task_id], negative)


def test_reddit_vote_is_blocked_without_independent_read_only_state(candidates):
    candidate = candidates[404]
    assert candidate["observer"]["kind"] == "pending_live_dom_contract"
    assert not _observed(candidate, '{"netScore":1}')
    assert candidate["qualification"].startswith("blocked")


def test_task441_pre_commit_value_and_post_commit_raw_file_have_paired_controls(candidates):
    candidate = candidates[441]
    before = candidate["pre_commit_observer"]
    expected = _frozen_fill(candidate)
    assert candidate["observer"]["expected_from_tool_call_index"] == 10
    assert before["position_before_tool_call_index"] == 13
    assert before["property"] == "value" and before["read_only"] is True
    assert hashlib.sha256(expected.encode()).hexdigest() == before["expected_text_sha256"]

    assert _pre_commit_observed(candidate, expected)
    assert not _pre_commit_observed(candidate, expected + expected)
    assert not _pre_commit_observed(candidate, expected.replace("GIVE ME SPACE", "old title"))

    assert _observed(candidate, expected)
    assert not _observed(candidate, expected + expected)
    assert not _observed(candidate, expected.replace("GIVE ME SPACE", "old title"))


def test_old_task441_double_document_review_is_not_a_latest_moli_result(candidates):
    review = json.loads(REVIEW.read_text(encoding="utf-8"))
    assert review["task_id"] == 441
    assert review["old_receipt_evaluator_status"] == "not_run"
    assert "old non-promotable" in review["evidence_class"]
    assert review["published_score_effect"] == "none"
    assert review["request_content_observation"] == {
        "doctype_count": 2,
        "head_end_count": 2,
        "title_count": 2,
        "target_title_present": True,
        "single_document_predicate": False,
    }
    assert review["offline_official_evaluator_check"]["score"] == 1.0
    assert candidates[441]["observer"]["require_no_other_title"] is True


@pytest.mark.parametrize("task_id", [399, 404, 415, 418, 441])
def test_official_evaluator_requires_exact_network_event_and_success_response(
    tmp_path, candidates, task_id: int
):
    benchmark = _configure_benchmark(hostname="192.168.5.2")
    rule = candidates[task_id]["official_network_event"]
    site_port = 9999 if task_id in {399, 404} else 8023
    url = f"http://192.168.5.2:{site_port}{rule['url_suffix']}"
    if task_id == 415:
        body = {"note": {"noteable_type": "MergeRequest", "note": "@davepgreene"}}
        mime = "application/json"
        request_body = json.dumps(body)
    elif task_id == 418:
        mime = "application/json"
        request_body = json.dumps({"availability": "busy", "clear_status_after": None})
    else:
        mime = "application/x-www-form-urlencoded"
        body = dict(rule["post_data"])
        if task_id == 441:
            body["content"] = "<!doctype html><head><title>GIVE ME SPACE</title></head></html>"
        request_body = urlencode(body)

    def score(*, target_url=url, status="SUCCESS", request_text=request_body):
        har = tmp_path / "network.har"
        har.write_text(
            json.dumps(
                {
                    "log": {
                        "version": "1.2",
                        "creator": {"name": "mutation-control", "version": "1"},
                        "entries": [
                            {
                                "request": {
                                    "method": rule["method"],
                                    "url": target_url,
                                    "headers": [],
                                    "postData": {"mimeType": mime, "text": request_text},
                                },
                                "response": {
                                    "status": rule.get("response_status", 200),
                                    "redirectURL": "",
                                    "headers": [],
                                    "cookies": [],
                                    "content": {"mimeType": "application/json", "text": "{}"},
                                },
                            }
                        ],
                    }
                }
            ),
            encoding="utf-8",
        )
        result = benchmark.evaluate_task(
            task_id=task_id,
            agent_response={
                "task_type": "MUTATE",
                "status": status,
                "retrieved_data": None,
                "error_details": None,
            },
            network_trace=har,
        )
        return result.score

    assert score() == 1.0
    assert score(target_url=url + "/wrong") == 0.0
    assert score(request_text="") == 0.0
    assert score(status="FAILURE") == 0.0
    if task_id == 441:
        duplicate_file = (
            "<!doctype html><head><title>GIVE ME SPACE</title></head></html>"
            "<!doctype html><head><title>old title</title></head></html>"
        )
        assert score(request_text=urlencode({**body, "content": duplicate_file})) == 1.0
        assert not _observed(candidates[441], duplicate_file)
