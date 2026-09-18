"""Paired controls for diagnostic mutation completion and official scoring."""

from __future__ import annotations

import hashlib
import json
import re
from html import unescape
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit

import pytest
from bs4 import BeautifulSoup

from harness.browser_use.runner import _configure_benchmark

ROOT = Path(__file__).resolve().parents[2]
CANDIDATES = (
    ROOT / "evaluation/trajectory-replay/webarena-verified/mutation-completion-candidates.json"
)
TRAJECTORIES = ROOT / "evaluation/trajectory-replay/webarena-verified/trajectories.json"
REVIEW = ROOT / "evaluation/trajectory-replay/webarena-verified/mutation-completion-review.json"
SHOPPING_ORIGIN = "http://host.docker.internal:7770"


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


def _observed(
    candidate: dict, body: str, path: str | None = None,
    origin: str = SHOPPING_ORIGIN,
) -> bool:
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
    if kind == "same_origin_get_json_discussions":
        value = json.loads(body)
        if (
            not isinstance(value, list)
            or len(value) >= observer["page_size"]
            or not all(
                isinstance(discussion, dict) and isinstance(discussion.get("notes"), list)
                for discussion in value
            )
        ):
            return False
        notes = [note for discussion in value for note in discussion["notes"]]
        return (
            sum(
                isinstance(note, dict)
                and note.get("noteable_id") == observer["noteable_id"]
                and note.get("noteable_type") == observer["noteable_type"]
                and note.get("noteable_iid") == observer["noteable_iid"]
                and isinstance(note.get("author"), dict)
                and note.get("author", {}).get("username") == observer["author_username"]
                and note.get("note") == observer["note_equals"]
                for note in notes
            )
            == 1
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
            if not _authenticated_sign_out(
                soup, observer["require_authenticated_sign_out_path"], origin
            ):
                return False
            matches = []
            for item in soup.select(observer["within"]):
                named = any(
                    link.get_text(" ", strip=True) == observer["product_name"]
                    for link in item.select("a")
                )
                edit = any(
                    _same_origin_path_matches(
                        link.get("href", ""), origin, observer["edit_href_path_pattern"]
                    )
                    for link in item.select("a")
                    if link.get_text(" ", strip=True) == "Edit"
                )
                if named and edit:
                    matches.append(item)
            return len(matches) == 1
        if candidate["task_id"] == 571:
            expected = " ".join(observer["address_lines"])
            for selector in observer["address_selectors"]:
                nodes = soup.select(selector)
                if len(nodes) != 1:
                    return False
                actual = re.sub(r"\s+", " ", nodes[0].get_text(" ", strip=True))
                if not actual.startswith(expected):
                    return False
            return True
        if candidate["task_id"] == 595:
            return len(soup.select(observer["selector"])) == 1
        if candidate["task_id"] == 731:
            title = soup.select_one("main article h1")
            author = soup.select_one(f'a[href="{observer["author_href"]}"]')
            paragraphs = [
                p.get_text(" ", strip=True)
                for p in soup.select(observer["body_selector"])
                if p.find_parent(observer["exclude_ancestor"]) is None
            ]
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


def _same_origin_path_matches(href: str, origin: str, path_pattern: str) -> bool:
    url = urlsplit(urljoin(origin + "/", href))
    return (
        f"{url.scheme}://{url.netloc}" == origin
        and re.fullmatch(path_pattern, url.path) is not None
    )


def _authenticated_sign_out(
    soup: BeautifulSoup, path: str, origin: str = SHOPPING_ORIGIN
) -> bool:
    matches = []
    for link in soup.select("a[href]"):
        if link.get_text(" ", strip=True) != "Sign Out":
            continue
        url = urlsplit(urljoin(origin + "/", link["href"]))
        if f"{url.scheme}://{url.netloc}" == origin and url.path == path:
            matches.append(link)
    return len(matches) == 1


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


def _order_observed(
    candidate: dict, success_text: str, history_html: str, detail_html: str, detail_path: str
) -> bool:
    """Exercise the dynamic order-number lookup followed by two independent GET bodies."""
    observer = candidate["observer"]
    numbers = re.findall(observer["order_number_pattern"], success_text)
    if len(numbers) != 1:
        return False
    number = numbers[0]
    history = BeautifulSoup(history_html, "html.parser")
    detail = BeautifulSoup(detail_html, "html.parser")
    sign_out_path = observer["require_authenticated_sign_out_path"]
    if not _authenticated_sign_out(history, sign_out_path) or not _authenticated_sign_out(
        detail, sign_out_path
    ):
        return False
    rows = [
        row
        for row in history.select("main table tr")
        if any(cell.get_text(" ", strip=True) == number for cell in row.select("td"))
    ]
    if len(rows) != 1:
        return False
    links = [
        urlsplit(link.get("href", ""))
        for link in rows[0].select("a")
        if link.get_text(" ", strip=True) == "View Order"
    ]
    if len(links) != 1 or links[0].netloc not in (
        "",
        "host.docker.internal:7770",
        "127.0.0.1:7770",
    ):
        return False
    if (
        re.fullmatch(observer["history_detail_href_path_pattern"], links[0].path) is None
        or links[0].path != detail_path
    ):
        return False
    headings = [node.get_text(" ", strip=True) for node in detail.select("main h1")]
    if headings != [observer["detail_heading_prefix"] + number]:
        return False
    tables = [
        table
        for table in detail.select("main table")
        if table.select_one("caption")
        and table.select_one("caption").get_text(" ", strip=True) == "Items Ordered"
    ]
    if len(tables) != 1:
        return False
    matching_items = [
        row
        for row in tables[0].select("tbody tr")
        if any(
            cell.get_text(" ", strip=True).startswith(observer["ordered_item_name"])
            for cell in row.select("td")
        )
        and re.search(
            rf"\bOrdered\s+{observer['ordered_item_quantity']}\b",
            row.get_text(" ", strip=True),
        )
    ]
    return len(matching_items) == 1


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
            '[{"notes":[{"noteable_id":138783,"noteable_type":"MergeRequest",'
            '"noteable_iid":40,"author":{"username":"byteblaze"},"note":"@davepgreene"}]}]',
            '[{"notes":[{"noteable_id":138783,"noteable_type":"MergeRequest",'
            '"noteable_iid":40,"author":{"username":"byteblaze"},"note":"other"}]}]',
        ),
        (418, '{"availability":"busy"}', '{"availability":"available"}'),
        (
            448,
            '{"username":"byteblaze","website_url":"https://egg.tart.com"}',
            '{"username":"byteblaze","website_url":"https://wrong.example"}',
        ),
        (
            571,
            '<div class="box-address-billing"><address>Emma Lopez<br>231 Willow Way<br>'
            'Suite 100<br>Chicago,  Illinois, 60601<br>United States</address></div>'
            '<div class="box-address-shipping"><address>Emma Lopez<br>231 Willow Way<br>'
            'Suite 100<br>Chicago,  Illinois, 60601<br>United States</address></div>',
            '<div class="box-address-billing"><address>Emma Lopez<br>231 Willow Way<br>'
            'Suite 100<br>Chicago, California, 60601<br>United States</address></div>'
            '<div class="box-address-shipping"><address>Emma Lopez<br>231 Willow Way<br>'
            'Suite 100<br>Chicago, Illinois, 60601<br>United States</address></div>',
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
            "&lt;p&gt;1 customer(s) love it!&lt;/p&gt;</textarea>",
            '<textarea name="product[short_description]">'
            "&lt;p&gt;0 customer(s) love it!&lt;/p&gt;</textarea>",
        ),
        (
            658,
            '[{"title":"401 bad gateway","due_date":"2030-12-31","assignees":[{"id":2264}]}]',
            '[{"title":"401 bad gateway","due_date":"2030-12-31","assignees":[{"id":3}]}]',
        ),
        (
            731,
            '<main><article><header><h1>Nvidia RTX 4090</h1>'
            '<p>Submitted by <a href="/user/MarvelsGrantMan136">author</a></p></header>'
            '<div class="submission__body"><p>Crazy device for ML!</p>'
            '<p>EDIT: This news aged well</p></div></article>'
            '<section><p>Unrelated comment</p></section></main>',
            '<main><article><header><h1>Nvidia RTX 4090</h1>'
            '<p>Submitted by <a href="/user/MarvelsGrantMan136">author</a></p></header>'
            '<div class="submission__body"><p>Crazy device for ML!</p></div></article>'
            '<section><p>EDIT: This news aged well</p></section></main>',
        ),
    ],
)
def test_read_only_observer_requires_persisted_state(
    candidates, task_id: int, positive: str, negative: str
):
    assert _observed(candidates[task_id], positive)
    assert not _observed(candidates[task_id], negative)


@pytest.mark.parametrize(
    "replacement",
    [
        {"noteable_id": 1},
        {"noteable_type": "Issue"},
        {"noteable_iid": 41},
        {"author": {"username": "another-user"}},
        {"author": None},
        {"note": "@someoneelse"},
    ],
)
def test_gitlab_note_observer_rejects_wrong_identity(candidates, replacement):
    note = {
        "noteable_id": 138783,
        "noteable_type": "MergeRequest",
        "noteable_iid": 40,
        "author": {"username": "byteblaze"},
        "note": "@davepgreene",
    }
    note.update(replacement)
    assert not _observed(candidates[415], json.dumps([{"notes": [note]}]))


def test_gitlab_note_observer_rejects_duplicate_exact_notes(candidates):
    note = {
        "noteable_id": 138783,
        "noteable_type": "MergeRequest",
        "noteable_iid": 40,
        "author": {"username": "byteblaze"},
        "note": "@davepgreene",
    }
    assert not _observed(candidates[415], json.dumps([{"notes": [note, note]}]))


def test_gitlab_note_observer_rejects_unknown_next_page(candidates):
    note = {
        "noteable_id": 138783,
        "noteable_type": "MergeRequest",
        "noteable_iid": 40,
        "author": {"username": "byteblaze"},
        "note": "@davepgreene",
    }
    discussions = [{"notes": [note]}] + [{"notes": []} for _ in range(19)]
    assert not _observed(candidates[415], json.dumps(discussions))


def test_reddit_vote_is_blocked_without_independent_read_only_state(candidates):
    candidate = candidates[404]
    assert candidate["observer"]["kind"] == "pending_live_dom_contract"
    assert not _observed(candidate, '{"netScore":1}')
    assert candidate["qualification"].startswith("blocked")


@pytest.mark.parametrize("task_id", [585])
def test_unqualified_persisted_state_stays_blocked(candidates, task_id: int):
    candidate = candidates[task_id]
    assert candidate["observer"]["kind"].startswith("pending_")
    assert candidate["qualification"].startswith("blocked")
    assert not _observed(candidate, '{"success":true}')


def test_order_observer_requires_success_number_history_and_matching_detail(candidates):
    candidate = candidates[506]
    assert candidate["observer"]["kind"] == "same_origin_get_order_history_detail"
    assert candidate["observer"]["history_path"] == "/sales/order/history/?limit=50"
    assert candidate["observer"]["read_only_get_count"] == 2
    assert candidate["observer"]["require_same_origin_sign_out_href"] is True
    success = "Thank you for your purchase! Your order number is: 000000190."
    sign_out = '<a href="/customer/account/logout/">Sign Out</a>'
    history = (
        sign_out + "<main><table><tr><td>000000190</td><td>"
        '<a href="/sales/order/view/order_id/190/">View Order</a></td></tr></table></main>'
    )
    detail = (
        sign_out + "<main><h1>Order # 000000190</h1><table><caption>Items Ordered</caption><tbody>"
        "<tr><td>Beyond Meat Beef Beefy Crumble, 5 Pound -- 2 per case.</td>"
        "<td>Ordered 1</td></tr></tbody></table></main>"
    )
    path = "/sales/order/view/order_id/190/"
    assert _order_observed(candidate, success, history, detail, path)
    absolute_sign_out = (
        '<a href="http://host.docker.internal:7770/customer/account/logout/">Sign Out</a>'
    )
    assert _order_observed(
        candidate, success, history.replace(sign_out, absolute_sign_out),
        detail.replace(sign_out, absolute_sign_out), path,
    )
    foreign_sign_out = '<a href="https://other.example/customer/account/logout/">Sign Out</a>'
    assert not _order_observed(
        candidate, success, history.replace(sign_out, foreign_sign_out), detail, path
    )
    assert not _order_observed(
        candidate, success, history, detail.replace(sign_out, foreign_sign_out), path
    )
    assert not _order_observed(
        candidate, success,
        history.replace("/customer/account/logout/", "/customer/account/login/"),
        detail, path,
    )
    assert not _order_observed(candidate, "Thank you for your purchase!", history, detail, path)
    assert not _order_observed(
        candidate, success, history.replace("000000190", "000000191"), detail, path
    )
    assert not _order_observed(candidate, success, history + history, detail, path)
    assert not _order_observed(
        candidate, success, history.replace("Sign Out", "Log In"), detail, path
    )
    assert not _order_observed(
        candidate, success, history, detail.replace("000000190", "000000191"), path
    )
    assert not _order_observed(
        candidate, success, history, detail.replace("Beyond Meat", "Other"), path
    )
    assert not _order_observed(
        candidate, success, history, detail.replace("Ordered 1", "Ordered 2"), path
    )
    assert not _order_observed(
        candidate, success, history, detail, "/sales/order/view/order_id/191/"
    )


def test_order_candidate_does_not_replace_any_of_four_official_events(candidates):
    candidate = candidates[506]
    assert candidate["official_network_evaluator_count"] == 4
    official = [
        row.expected.model_dump(exclude_none=True)
        for row in _configure_benchmark(hostname="192.168.5.2").get_task(506).eval
        if row.evaluator == "NetworkEventEvaluator"
    ]
    assert [(row["http_method"], row["response_status"]) for row in official] == [
        ("POST", 200),
        ("GET", 200),
        ("POST", 200),
        ("GET", 200),
    ]
    assert official[0]["post_data"]["product"] == "22490"
    assert official[1]["response_content"]["items_qty"] == 1
    assert (
        official[1]["response_content"]["$.items[0].name"]
        == candidate["observer"]["ordered_item_name"]
    )
    assert official[2]["post_data"]["$.billingAddress.customerId"] == "27"
    assert official[3]["url"].endswith("/checkout/onepage/success")


def test_wishlist_observer_binds_product_id_name_and_authenticated_item(candidates):
    candidate = candidates[465]
    assert candidate["observer"]["kind"] == "same_origin_get_html"
    assert candidate["observer"]["href_origin"] == "response_url_origin"
    assert not candidate["qualification"].startswith("blocked")
    assert candidate["observer"]["require_same_origin_sign_out_href"] is True
    name = "Tide PODS Spring Meadow Scent HE Turbo Laundry Detergent Pacs, 81 Count"
    item = (
        '<main><form><li><a href="/tide-pods.html">'
        + name
        + '</a><a href="/wishlist/index/configure/id/3/product_id/71337/">Edit</a>'
        "</li></form></main>"
    )
    signed_in = '<header><a href="/customer/account/logout/">Sign Out</a></header>'
    assert _observed(candidate, signed_in + item)
    assert _observed(candidate, signed_in.replace(
        '/customer/account/logout/',
        'http://host.docker.internal:7770/customer/account/logout/') + item)
    assert not _observed(candidate, signed_in.replace(
        '/customer/account/logout/',
        'https://other.example/customer/account/logout/') + item)
    assert not _observed(candidate, signed_in.replace(
        '/customer/account/logout/', '/customer/account/login/') + item)
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
    rainbond_origin = "http://webarena-slice-warm-1-195-shopping.dev.lexmount.net"
    absolute_live_shape = (signed_in + item).replace(
        'href="/', f'href="{rainbond_origin}/'
    )
    assert _observed(candidate, absolute_live_shape, origin=rainbond_origin)
    assert not _observed(candidate, absolute_live_shape, origin=SHOPPING_ORIGIN)
    assert not _observed(
        candidate,
        absolute_live_shape.replace("product_id/71337/", "product_id/71338/"),
        origin=rainbond_origin,
    )


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
