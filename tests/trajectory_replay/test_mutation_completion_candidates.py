"""Paired controls for diagnostic mutation completion and official scoring."""

from __future__ import annotations

import hashlib
import json
import re
from html import unescape
from pathlib import Path
from urllib.parse import urlencode, urlsplit

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
    assert set(candidates) == {
        399,
        404,
        415,
        418,
        441,
        448,
        465,
        506,
        571,
        576,
        585,
        590,
        595,
        464,
        620,
        640,
        645,
        658,
        666,
        714,
        731,
    }
    for task_id, candidate in candidates.items():
        source = trajectories[task_id]
        assert candidate["action_count"] == len(source["tool_calls"])
        assert source["evaluation_input"]["status"] == "SUCCESS"
        task = benchmark.get_task(task_id)
        assert task.revision == 2
        official = [row for row in task.eval if row.evaluator == "NetworkEventEvaluator"]
        if task_id == 506:
            assert len(official) == candidate["official_network_evaluator_count"] == 4
        else:
            assert len(official) == 1
            expected = official[0].expected.model_dump(exclude_none=True)
            event = candidate["official_network_event"]
            assert expected["http_method"] == event["method"]
            if "url_pattern" in event:
                assert expected["url"] == event["url_pattern"]
                assert expected["post_data"] == event["post_data"]
            elif isinstance(event["url_suffix"], list):
                assert isinstance(expected["url"], list)
                assert [url.split("__REDDIT__")[-1] for url in expected["url"]] == event[
                    "url_suffix"
                ]
                if "referer_suffix" in event:
                    assert expected["headers"]["referer"].endswith(event["referer_suffix"])
                if "response_content" in event:
                    assert expected["response_content"] == event["response_content"]
            else:
                assert expected["url"].endswith(event["url_suffix"])
                assert expected.get("post_data") == event["post_data"]
            if "response_status" in event:
                assert expected["response_status"] == event["response_status"]
        assert candidate["observer"]["kind"].startswith(
            ("same_origin_get", "current_redirect_get", "pending_")
        )
        assert "official" in candidate["qualification"]
        assert "evaluator" in candidate["qualification"]


def _observed(candidate: dict, body: str, path: str | None = None) -> bool:
    """Exercise the proposed read-only predicates on synthetic response bodies."""
    observer = candidate["observer"]
    kind = observer["kind"]
    if kind == "same_origin_get_json":
        value = json.loads(body)
        return value.get(observer["json_key"]) == observer["equals"] and (
            "require_authenticated_user" not in observer
            or candidate["task_id"] == 418
            or value.get("username") == observer["require_authenticated_user"]
        )
    if kind == "same_origin_get_json_pair":
        values = json.loads(body)
        return (
            isinstance(values, list)
            and sorted(row.get("id") for row in values) == observer["require_user_ids"]
            and all(row.get("access_level") == observer["require_access_level"] for row in values)
        )
    if kind == "same_origin_get_json_collection":
        values = json.loads(body)
        return (
            isinstance(values, list)
            and sum(
                all(row.get(key) == expected for key, expected in observer["fields"].items())
                and (
                    "assignee_id" not in observer
                    or any(
                        assignee.get("id") == observer["assignee_id"]
                        for assignee in row.get("assignees", [])
                    )
                )
                for row in values
            )
            == 1
        )
    if kind == "current_redirect_get_html":
        if path is None or re.fullmatch(observer["path_pattern"], path) is None:
            return False
        soup = BeautifulSoup(body, "html.parser")
        titles = [node.get_text(" ", strip=True) for node in soup.select("main article h1")]
        if titles != [observer["title"]]:
            return False
        if not soup.select_one(f'a[href="{observer["forum_href"]}"]'):
            return False
        if not soup.select_one(f'a[href="{observer["author_href"]}"]'):
            return False
        return "body" not in observer or any(
            node.get_text(" ", strip=True) == observer["body"]
            for node in soup.select("main article p")
        )
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
        if candidate["task_id"] == 464:
            fields = soup.select(observer["selector"])
            return (
                len(fields) == 1 and unescape(fields[0].decode_contents()) == observer["html_value"]
            )
        if candidate["task_id"] == 465:
            if (
                len(soup.select(f'a[href="{observer["require_authenticated_sign_out_href"]}"]'))
                != 1
            ):
                return False
            matches = []
            for item in soup.select(observer["within"]):
                named = any(
                    link.get_text(" ", strip=True) == observer["product_name"]
                    for link in item.select("a")
                )
                edit = any(
                    urlsplit(link.get("href", "")).netloc
                    in ("", "host.docker.internal:7770", "127.0.0.1:7770")
                    and re.fullmatch(
                        observer["edit_href_path_pattern"], urlsplit(link.get("href", "")).path
                    )
                    for link in item.select("a")
                    if link.get_text(" ", strip=True) == "Edit"
                )
                if named and edit:
                    matches.append(item)
            return len(matches) == 1
        if candidate["task_id"] == 571:
            for selector, expected in observer["fields"].items():
                nodes = soup.select(selector)
                if len(nodes) != 1:
                    return False
                actual = (
                    nodes[0].select_one("option[selected]").get("value")
                    if nodes[0].name == "select" and nodes[0].select_one("option[selected]")
                    else nodes[0].get("value")
                )
                if actual != expected:
                    return False
            return True
        if candidate["task_id"] == 595:
            return len(soup.select(observer["selector"])) == 1
        if candidate["task_id"] == 731:
            title = soup.select_one("main article h1")
            author = soup.select_one(f'a[href="{observer["author_href"]}"]')
            paragraphs = [p.get_text(" ", strip=True) for p in soup.select("main article p")]
            return (
                title is not None
                and title.get_text(" ", strip=True) == observer["title"]
                and author is not None
                and paragraphs == observer["body_exact_lines"]
            )
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
        (
            448,
            '{"username":"byteblaze","website_url":"https://egg.tart.com"}',
            '{"username":"byteblaze","website_url":"https://wrong.example"}',
        ),
        (
            571,
            '<input name="firstname" value="Emma"><input name="lastname" value="Lopez">'
            '<input name="street[0]" value="231 Willow Way">'
            '<input name="street[1]" value="Suite 100">'
            '<select name="country_id"><option value="US" selected>United States</option></select>'
            '<select name="region_id"><option value="23" selected>Illinois</option></select>'
            '<input name="city" value="Chicago"><input name="postcode" value="60601">',
            '<input name="firstname" value="Emma"><input name="lastname" value="Lopez">'
            '<input name="street[0]" value="old">'
            '<input name="street[1]" value="Suite 100">'
            '<select name="country_id"><option value="US" selected>United States</option></select>'
            '<select name="region_id"><option value="23" selected>Illinois</option></select>'
            '<input name="city" value="Chicago"><input name="postcode" value="60601">',
        ),
        (
            576,
            '[{"id":5,"access_level":30},{"id":1842,"access_level":30}]',
            '[{"id":5,"access_level":20},{"id":1842,"access_level":30}]',
        ),
        (
            590,
            '[{"title":"product launch","start_date":"2023-01-16","due_date":"2023-01-30"}]',
            '[{"title":"product launch","start_date":"2023-01-16","due_date":"2023-01-31"}]',
        ),
        (
            595,
            '<button class="subscribe-button subscribe-button--unsubscribe">Unsubscribe</button>',
            '<button class="subscribe-button">Subscribe</button>',
        ),
        (
            464,
            '<textarea name="product[short_description]">'
            '&lt;p&gt;1 customer(s) love it!&lt;/p&gt;</textarea>',
            '<textarea name="product[short_description]">'
            '&lt;p&gt;0 customer(s) love it!&lt;/p&gt;</textarea>',
        ),
        (
            658,
            '[{"title":"401 bad gateway","due_date":"2030-12-31","assignees":[{"id":2264}]}]',
            '[{"title":"401 bad gateway","due_date":"2030-12-31","assignees":[{"id":3}]}]',
        ),
        (
            731,
            '<main><article><h1>Nvidia RTX 4090</h1><a href="/user/MarvelsGrantMan136">author</a>'
            "<p>Crazy device for ML!</p><p>EDIT: This news aged well</p></article></main>",
            '<main><article><h1>Nvidia RTX 4090</h1><a href="/user/MarvelsGrantMan136">author</a>'
            "<p>Crazy device for ML!</p></article></main>",
        ),
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


@pytest.mark.parametrize("task_id", [506, 585])
def test_unqualified_persisted_state_stays_blocked(candidates, task_id: int):
    candidate = candidates[task_id]
    assert candidate["observer"]["kind"].startswith("pending_")
    assert candidate["qualification"].startswith("blocked")
    assert not _observed(candidate, '{"success":true}')


def test_wishlist_observer_binds_product_id_name_and_authenticated_item(candidates):
    candidate = candidates[465]
    assert candidate["observer"]["kind"] == "same_origin_get_html"
    assert not candidate["qualification"].startswith("blocked")
    name = "Tide PODS Spring Meadow Scent HE Turbo Laundry Detergent Pacs, 81 Count"
    item = (
        '<main><form><li><a href="/tide-pods.html">'
        + name
        + '</a><a href="/wishlist/index/configure/id/3/product_id/71337/">Edit</a>'
        "</li></form></main>"
    )
    signed_in = '<header><a href="/customer/account/logout/">Sign Out</a></header>'
    assert _observed(candidate, signed_in + item)
    assert _observed(
        candidate,
        signed_in
        + item.replace(
            "/wishlist/index/configure/",
            "http://host.docker.internal:7770/wishlist/index/configure/",
        ),
    )
    assert not _observed(candidate, item)
    assert not _observed(candidate, signed_in + item.replace("71337", "71338"))
    assert not _observed(
        candidate,
        signed_in
        + item.replace(
            "/wishlist/index/configure/", "https://other.example/wishlist/index/configure/"
        ),
    )
    assert not _observed(candidate, signed_in + item.replace(name, "Other product"))
    assert not _observed(candidate, signed_in + item + item)
    separate_items = item.replace(
        '<a href="/wishlist/index/configure/id/3/product_id/71337/">Edit</a>',
        '</li><li><a href="/wishlist/index/configure/id/3/product_id/71337/">Edit</a>',
    )
    assert not _observed(candidate, signed_in + separate_items)
    assert not _observed(candidate, signed_in + "<p>1 item</p>")


@pytest.mark.parametrize(
    ("task_id", "forum", "title", "body"),
    [
        (620, "relationship_advice", "deal with long-distance relationships", "Please help"),
        (640, "books", "book reading Meet up!", "virtual meetup for book reading on March 15th"),
        (645, "books", "What could machine learning help the books field?", None),
    ],
)
def test_new_forum_post_requires_fresh_numeric_permalink_and_saved_content(
    candidates, task_id: int, forum: str, title: str, body: str | None
):
    candidate = candidates[task_id]
    path = f"/f/{forum}/2/new-post"
    article = (
        f'<main><article><h1>{title}</h1><a href="/f/{forum}">forum</a>'
        '<a href="/user/MarvelsGrantMan136">author</a>'
        + (f"<p>{body}</p>" if body else "")
        + "</article></main>"
    )
    assert _observed(candidate, article, path)
    assert not _observed(candidate, article, f"/f/{forum}/new-post")
    assert not _observed(candidate, article.replace(title, "wrong title"), path)
    if body:
        assert not _observed(candidate, article.replace(body, "wrong body"), path)


@pytest.mark.parametrize("task_id", [666, 714])
def test_remaining_unqualified_inputs_stay_blocked(candidates, task_id: int):
    candidate = candidates[task_id]
    assert candidate["observer"]["kind"].startswith("pending_")
    assert candidate["qualification"].startswith("blocked")
    if task_id == 666:
        gate = candidate["input_gate"]
        source = next(
            row
            for row in json.loads(TRAJECTORIES.read_text(encoding="utf-8"))["trajectories"]
            if row["task_id"] == 666
        )
        assert (
            source["tool_calls"][gate["tool_call_index"]]["selector"] == gate["qualified_selector"]
        )
        assert gate["qualified_selector"] != gate["recompiled_selector"]


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


@pytest.mark.parametrize("task_id", [399, 404, 415, 418, 441, 448, 571, 576, 585, 590, 595])
def test_official_evaluator_requires_exact_network_event_and_success_response(
    tmp_path, candidates, task_id: int
):
    benchmark = _configure_benchmark(hostname="192.168.5.2")
    rule = candidates[task_id]["official_network_event"]
    site_port = 9999 if task_id in {399, 404, 595} else 7770 if task_id in {571, 585} else 8023
    suffix = rule["url_suffix"]
    if isinstance(suffix, list):
        suffix = suffix[0]
    url = f"http://192.168.5.2:{site_port}{suffix}"
    if task_id == 415:
        body = {"note": {"noteable_type": "MergeRequest", "note": "@davepgreene"}}
        mime = "application/json"
        request_body = json.dumps(body)
    elif task_id in {418, 576}:
        mime = "application/json"
        request_body = json.dumps(
            {"availability": "busy", "clear_status_after": None}
            if task_id == 418
            else {"user_id": "5,1842", "access_level": 30}
        )
    elif task_id == 595:
        mime = "application/json"
        request_body = "{}"
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
                                    "headers": (
                                        [
                                            {
                                                "name": "referer",
                                                "value": "http://192.168.5.2:9999"
                                                + rule["referer_suffix"],
                                            }
                                        ]
                                        if task_id == 595
                                        else []
                                    ),
                                    "postData": {"mimeType": mime, "text": request_text},
                                },
                                "response": {
                                    "status": rule.get("response_status", 200),
                                    "redirectURL": "",
                                    "headers": [],
                                    "cookies": [],
                                    "content": {
                                        "mimeType": "application/json",
                                        "text": '{"subscribed":true}' if task_id == 595 else "{}",
                                    },
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
    if "post_data" in rule:
        assert score(request_text="") == 0.0
    assert score(status="FAILURE") == 0.0
    if task_id == 441:
        duplicate_file = (
            "<!doctype html><head><title>GIVE ME SPACE</title></head></html>"
            "<!doctype html><head><title>old title</title></head></html>"
        )
        assert score(request_text=urlencode({**body, "content": duplicate_file})) == 1.0
        assert not _observed(candidates[441], duplicate_file)
